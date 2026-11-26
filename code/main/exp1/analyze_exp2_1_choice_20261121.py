#!/usr/bin/env python3
"""실험 2-1회 선택 모형 분석: 요청마다 직전 공개 지표를 붙이고 조건부 로짓으로 가중치를 추정한다.

설계: 02_분석/28_실험설계_2-1회_기본라우팅과_실시간지표_20261120.md 6절(B. 선택 단위 조건부 로짓)
결과 문서: 02_분석/29_실험2-1회_중간분석_기본라우팅_중요지표_20261121.md

입력: exp2_1_routing_20261120.py 의 출력 폴더(public/, routing/, generation/)
필요: numpy (VM 분석용 venv /opt/mango-bench/anvenv)

절차
1. 공개 스냅숏을 엔드포인트별 특징으로 줄인다.
   - stats/endpoint(5분): TTFT p50, 처리 속도 p50, 30분 요청 수, 게시 입력·출력 단가, 태그
   - effective-pricing(10분): 실효 입력·출력 단가, 캐시 적중률
   - /api/v1/models/.../endpoints(1분): uptime_last_5m·30m (태그로 연결)
   - tool-call-error-rate·structured-output-error-rate(15분 수집, 1시간 칸): 직전 **확정 칸**(끝에서 두 번째)
   - cache-hit-rate-comparison(15분 수집, 1시간 칸): 가장 최근 칸(주)과 직전 확정 칸(민감도)
   - 1시간 실효 입력 단가 = 입력 게시가 × (1 − h) + 캐시 읽기 단가 × h (h = 1시간 캐시 적중률).
     캐시 읽기 단가가 없거나 적중률이 없으면 h = 0(게시가 그대로).
2. 요청마다(label=main, 첫 시도 엔드포인트가 있는 것) 요청 시각 이전의 가장 최근 스냅숏을 붙인다(as-of).
   허용 시차: endpoint 10분, effective-pricing 20분, uptime 5분, 오류율 30분. 넘으면 그 요청은 제외.
3. 후보 집합: 그 시각 stats/endpoint 목록 중 이번 실행 동안 해당 모델에서 한 번이라도 첫 시도로
   뽑힌 엔드포인트(주 분석). 계정 설정·요청 조건으로 받을 수 없는 엔드포인트를 빼기 위한 경험적 기준이다.
   보조로 목록 전체(stats가 있는 곳)도 맞춘다.
4. 조건부 로짓(뉴턴법). 변수는 후보 전체에서 표준화해 계수 크기를 비교한다.
5. 불확실성: 시간(UTC 시) 단위 군집 부트스트랩. 예측력: 연속 시간 블록을 빼는 교차검증 로그 손실.

사용:
  /opt/mango-bench/anvenv/bin/python analyze_exp2_1_choice_20261121.py --run <run 폴더> --out <결과.json> [--boot 200]
"""
import argparse
import bisect
import glob
import gzip
import json
import math
from collections import Counter, defaultdict

import numpy as np

MODELS = ["deepseek-v4.1-flash-1017", "glm-5.3", "glm-5.3-flash", "kimi-k3.1-pro"]
OUT_TOKENS = 16           # 본 실험 max_tokens
LAGS = {"ep": 600, "eff": 1200, "up": 300, "err": 1800}


def rd(folder):
    for p in sorted(glob.glob(folder + "/*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ 1. 스냅숏
def load_public(run):
    snaps = {k: defaultdict(list) for k in ("ep", "eff", "up", "tool", "so", "cache", "cache_prev")}
    for r in rd(run + "/public"):
        if r.get("status") != 200:
            continue
        mk, api, t = r.get("model_key"), r.get("api"), r.get("request_ts")
        try:
            body = json.loads(r["body"])["data"]
        except (ValueError, KeyError, TypeError):
            continue
        if api == "endpoint":
            d = {}
            for x in body:
                s = x.get("stats") or {}
                pr = x.get("pricing") or {}
                d[x["id"]] = {"ttft": fnum(s.get("p50_latency")), "tps": fnum(s.get("p50_throughput")),
                              "req": fnum(s.get("request_count")) or 0.0,
                              "pin": fnum(pr.get("prompt")), "pout": fnum(pr.get("completion")),
                              "pcache": fnum(pr.get("input_cache_read")),
                              "tag": x.get("provider_slug"), "name": x.get("provider_name"),
                              "quant": x.get("quantization"), "deranked": bool(x.get("is_deranked"))}
            snaps["ep"][mk].append((t, d))
        elif api == "effective-pricing":
            d = {p["endpointId"]: {"ein": fnum(p.get("effectiveInputPrice")),
                                   "eout": fnum(p.get("effectiveOutputPrice")),
                                   "cache": fnum(p.get("cacheHitRate"))}
                 for p in body.get("providerSummaries") or []}
            snaps["eff"][mk].append((t, d))
        elif api == "models-endpoints":
            d = {x.get("tag"): {"up5": fnum(x.get("uptime_last_5m")), "up30": fnum(x.get("uptime_last_30m"))}
                 for x in (body.get("endpoints") or [])}
            snaps["up"][mk].append((t, d))
        elif api == "cache-hit-rate-comparison":
            if body:
                snaps["cache"][mk].append((t, {e: fnum(v) for e, v in body[-1]["y"].items()}))
            if len(body) >= 2:
                snaps["cache_prev"][mk].append((t, {e: fnum(v) for e, v in body[-2]["y"].items()}))
        elif api in ("tool-call-error-rate", "structured-output-error-rate"):
            if len(body) >= 2:
                b = body[-2]          # 끝 칸은 진행 중 → 직전 확정 칸
                snaps["tool" if api[0] == "t" else "so"][mk].append((t, {e: fnum(v) for e, v in b["y"].items()}))
    for k in snaps:
        for mk in snaps[k]:
            snaps[k][mk].sort(key=lambda z: z[0])
    return snaps


def asof(series, t, lag):
    ts = [z[0] for z in series]
    i = bisect.bisect_right(ts, t) - 1
    if i < 0 or t - ts[i] > lag:
        return None
    return series[i][1]


# ------------------------------------------------------------------ 2. 요청
def load_requests(run, label="main"):
    gens = {}
    for g in rd(run + "/generation"):
        if g.get("status") != 200:
            continue
        try:
            d = json.loads(g["body"])["data"]
        except (ValueError, KeyError, TypeError):
            continue
        pr = d.get("provider_responses") or []
        if pr:
            gens[g["resp_id"]] = {"first": pr[0].get("endpoint_id"), "n_att": len(pr),
                                  "in_tok": d.get("tokens_prompt") or d.get("native_tokens_prompt")}
    reqs = []
    for r in rd(run + "/routing"):
        if r.get("label") != label or r.get("status") != 200:
            continue
        g = gens.get(r.get("resp_id"))
        if g and g["first"]:
            reqs.append({"t": r["request_ts"], "mk": r["model_key"], "chosen": g["first"],
                         "in_tok": g["in_tok"], "n_att": g["n_att"]})
    reqs.sort(key=lambda z: z["t"])
    return reqs


# ------------------------------------------------------------------ 3. 선택 자료
FEATS = ["log_cost", "log_cost_eff1h", "log_pin", "log_pout", "log_ein", "log_ein1h", "log_ein1h_prev", "log_ttft", "log_tps", "tool_err", "so_err", "up30",
         "log_share", "stats_missing"]


def build(reqs, snaps, eligible_only=True):
    ever = defaultdict(set)
    for q in reqs:
        ever[q["mk"]].add(q["chosen"])
    choices, drop = [], Counter()
    for q in reqs:
        mk, t = q["mk"], q["t"]
        ep = asof(snaps["ep"][mk], t, LAGS["ep"])
        if ep is None:
            drop["no_ep_snapshot"] += 1
            continue
        eff = asof(snaps["eff"][mk], t, LAGS["eff"]) or {}
        up = asof(snaps["up"][mk], t, LAGS["up"]) or {}
        tool = asof(snaps["tool"][mk], t, LAGS["err"]) or {}
        so = asof(snaps["so"][mk], t, LAGS["err"]) or {}
        ch = asof(snaps["cache"][mk], t, LAGS["err"]) or {}
        chp = asof(snaps["cache_prev"][mk], t, LAGS["err"]) or {}
        cand = [e for e in ep if (e in ever[mk] if eligible_only else ep[e]["ttft"] is not None or e in ever[mk])]
        if q["chosen"] not in cand:
            drop["chosen_not_in_candidates"] += 1
            continue
        tot_req = sum(ep[e]["req"] for e in cand) or 1.0
        ttfts = [ep[e]["ttft"] for e in cand if ep[e]["ttft"]]
        tpss = [ep[e]["tps"] for e in cand if ep[e]["tps"]]
        med_ttft = float(np.median(ttfts)) if ttfts else 1000.0
        med_tps = float(np.median(tpss)) if tpss else 50.0
        in_tok = q["in_tok"] or 100
        rows = []
        for e in cand:
            x = ep[e]
            pin = max(x["pin"] or 0.0, 1e-10)
            pout = max(x["pout"] or 0.0, 1e-10)
            ein = (eff.get(e) or {}).get("ein")
            pc = x["pcache"]

            def eff_in(rate):
                if pc is None or rate is None:
                    return pin
                h = min(max(rate / 100.0, 0.0), 1.0)
                return pin * (1 - h) + max(pc, 0.0) * h
            ein1h, ein1hp = max(eff_in(ch.get(e)), 1e-10), max(eff_in(chp.get(e)), 1e-10)
            miss = x["ttft"] is None or x["tps"] is None
            u = up.get(x["tag"]) or {}
            rows.append({
                "e": e,
                "log_cost": math.log(in_tok * pin + OUT_TOKENS * pout),
                "log_cost_eff1h": math.log(in_tok * ein1h + OUT_TOKENS * pout),
                "log_ein1h": math.log(ein1h * 1e6), "log_ein1h_prev": math.log(ein1hp * 1e6),
                "log_pin": math.log(pin * 1e6), "log_pout": math.log(pout * 1e6),
                "log_ein": math.log(max(ein, 1e-4)) if ein else math.log(pin * 1e6),
                "log_ttft": math.log(x["ttft"] or med_ttft), "log_tps": math.log(x["tps"] or med_tps),
                "tool_err": tool.get(e) if tool.get(e) is not None else 0.0,
                "so_err": so.get(e) if so.get(e) is not None else 0.0,
                "up30": u.get("up30") if u.get("up30") is not None else 100.0,
                "log_share": math.log(x["req"] / tot_req + 1e-4),
                "stats_missing": 1.0 if miss else 0.0,
            })
        choices.append({"t": q["t"], "mk": mk, "hour": int(q["t"] // 3600), "chosen": q["chosen"], "rows": rows})
    return choices, drop


def tensor(choices, feats):
    N = len(choices)
    K = max(len(c["rows"]) for c in choices)
    X = np.zeros((N, K, len(feats)))
    M = np.zeros((N, K), dtype=bool)
    y = np.zeros(N, dtype=int)
    for i, c in enumerate(choices):
        for k, r in enumerate(c["rows"]):
            X[i, k] = [r[f] for f in feats]
            M[i, k] = True
            if r["e"] == c["chosen"]:
                y[i] = k
    return X, M, y


def standardize(X, M):
    v = X[M]
    mu, sd = v.mean(0), v.std(0)
    sd[sd == 0] = 1.0
    return (X - mu) / sd, mu, sd


def fit(X, M, y, iters=50):
    N, K, p = X.shape
    b = np.zeros(p)
    for _ in range(iters):
        u = np.where(M, X @ b, -np.inf)
        u -= u.max(1, keepdims=True)
        w = np.exp(u)
        P = w / w.sum(1, keepdims=True)
        xbar = (P[..., None] * X).sum(1)
        g = (X[np.arange(N), y] - xbar).sum(0)
        D = X - xbar[:, None, :]
        H = -np.einsum("nk,nki,nkj->ij", P, D, D)
        try:
            step = np.linalg.solve(H - 1e-8 * np.eye(p), g)
        except np.linalg.LinAlgError:
            break
        b -= step
        if np.abs(step).max() < 1e-8:
            break
    u = np.where(M, X @ b, -np.inf)
    ll = float((u[np.arange(N), y] - np.logaddexp.reduce(u, axis=1)).sum())
    try:
        se = np.sqrt(np.diag(np.linalg.inv(-H)))
    except np.linalg.LinAlgError:
        se = np.full(p, np.nan)
    return b, se, ll


def ll_of(X, M, y, b):
    u = np.where(M, X @ b, -np.inf)
    return u[np.arange(len(y)), y] - np.logaddexp.reduce(u, axis=1)


def ll_uniform(M):
    return -np.log(M.sum(1))


SPECS = {
    "S1_cost": ["log_cost"],
    "S2_cost_ttft": ["log_cost", "log_ttft"],
    "S3_cost_ttft_tps": ["log_cost", "log_ttft", "log_tps"],
    "S4_main": ["log_cost", "log_ttft", "log_tps", "tool_err", "so_err", "up30", "stats_missing"],
    "S5_split_price": ["log_pin", "log_pout", "log_ttft", "log_tps", "tool_err", "so_err", "up30", "stats_missing"],
    "S6_effective_in": ["log_ein", "log_pout", "log_ttft", "log_tps", "tool_err", "so_err", "up30", "stats_missing"],
    "S7_main_plus_share": ["log_cost", "log_ttft", "log_tps", "tool_err", "so_err", "up30", "stats_missing",
                           "log_share"],
    "S8_eff1h_split": ["log_ein1h", "log_pout", "log_ttft", "log_tps", "tool_err", "so_err", "up30", "stats_missing"],
    "S9_eff1h_cost": ["log_cost_eff1h", "log_ttft", "log_tps", "tool_err", "so_err", "up30", "stats_missing"],
    "S10_eff1h_prev_split": ["log_ein1h_prev", "log_pout", "log_ttft", "log_tps", "tool_err", "so_err", "up30",
                             "stats_missing"],
    "B_share_only": ["log_share"],
}
BOOT_SPECS = {"S4_main", "S5_split_price", "S8_eff1h_split", "S9_eff1h_cost"}


def run_specs(choices, boot, rng, cv_blocks=4):
    res = {}
    hours = np.array([c["hour"] for c in choices])
    uh = np.unique(hours)
    block = {h: i * cv_blocks // len(uh) for i, h in enumerate(uh)}
    blk = np.array([block[h] for h in hours])
    for name, feats in SPECS.items():
        X, M, y = tensor(choices, feats)
        Xs, mu, sd = standardize(X, M)
        b, se, ll = fit(Xs, M, y)
        ll0 = float(ll_uniform(M).sum())
        # 시간 군집 부트스트랩
        bs = []
        idx_by_h = {h: np.where(hours == h)[0] for h in uh}
        for _ in range(boot if name in BOOT_SPECS else 0):
            pick = rng.choice(uh, size=len(uh), replace=True)
            idx = np.concatenate([idx_by_h[h] for h in pick])
            bb, _, _ = fit(Xs[idx], M[idx], y[idx], iters=25)
            bs.append(bb)
        bs = np.array(bs) if bs else np.zeros((0, len(feats)))
        # 블록 교차검증
        cvll = []
        for k in range(cv_blocks):
            tr, te = blk != k, blk == k
            if te.sum() == 0:
                continue
            bk, _, _ = fit(Xs[tr], M[tr], y[tr], iters=30)
            cvll.append(ll_of(Xs[te], M[te], y[te], bk))
        cvll = np.concatenate(cvll)
        res[name] = {
            "feats": feats, "n": int(len(y)), "mean_candidates": float(M.sum(1).mean()),
            "coef_std": b.tolist(), "se_std": se.tolist(),
            "boot_ci95": (np.percentile(bs, [2.5, 97.5], axis=0).T.tolist() if len(bs) else None),
            "coef_raw": (b / sd).tolist(), "sd": sd.tolist(),
            "ll": ll, "ll_uniform": ll0, "mcfadden_r2": 1 - ll / ll0,
            "cv_logloss": float(-cvll.mean()), "cv_logloss_uniform": float(ll_uniform(M).mean()),
        }
    return res


def feature_corr(choices, feats):
    X, M, _ = tensor(choices, feats)
    v = X[M]
    return np.corrcoef(v.T).round(3).tolist()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--boot", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20261121)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)

    snaps = load_public(a.run)
    reqs = load_requests(a.run)
    out = {"run": a.run, "n_requests": len(reqs),
           "window_utc": [reqs[0]["t"], reqs[-1]["t"]] if reqs else None, "lags_s": LAGS}
    for elig in (True, False):
        tag = "eligible" if elig else "all_listed"
        ch, drop = build(reqs, snaps, eligible_only=elig)
        sec = {"n_choices": len(ch), "dropped": dict(drop), "pooled": run_specs(ch, a.boot if elig else 0, rng)}
        if elig:
            sec["by_model"] = {}
            for mk in MODELS:
                cm = [c for c in ch if c["mk"] == mk]
                if len(cm) > 200:
                    sec["by_model"][mk] = run_specs(cm, 0, rng)
            sec["feature_corr"] = {"feats": FEATS, "r": feature_corr(ch, FEATS)}
            sec["hourly_main"] = {}
            for h in sorted({c["hour"] for c in ch}):
                chh = [c for c in ch if c["hour"] == h]
                X, M, y = tensor(chh, SPECS["S8_eff1h_split"])
                Xs, _, sd = standardize(X, M)
                b, se, ll = fit(Xs, M, y, iters=30)
                sec["hourly_main"][str(h)] = {"n": len(chh), "coef_std": b.tolist()}
        out[tag] = sec
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    # 요약 출력
    for tag in ("eligible", "all_listed"):
        sec = out[tag]
        print(f"\n=== {tag}: choices {sec['n_choices']} dropped {sec['dropped']}")
        for name, r in sec["pooled"].items():
            cs = ", ".join(f"{f}={c:+.2f}" + (f"[{ci[0]:+.2f},{ci[1]:+.2f}]" if r["boot_ci95"] else "")
                           for f, c, ci in zip(r["feats"], r["coef_std"], r["boot_ci95"] or [[0, 0]] * 12))
            print(f"{name}: R2={r['mcfadden_r2']:.3f} cvLL={r['cv_logloss']:.3f} (unif {r['cv_logloss_uniform']:.3f}) | {cs}")
    for mk, rs in out["eligible"]["by_model"].items():
        r = rs["S8_eff1h_split"]
        print(f"\n[{mk}] S8 R2={r['mcfadden_r2']:.3f} cvLL={r['cv_logloss']:.3f}/{r['cv_logloss_uniform']:.3f} | "
              + ", ".join(f"{f}={c:+.2f}" for f, c in zip(r["feats"], r["coef_std"])))


if __name__ == "__main__":
    main()
