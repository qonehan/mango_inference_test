#!/usr/bin/env python3
"""2-2회 데이터만으로 환산하고, 2-1회 선택 모형으로 2-2회 4회차 측정 시간대의 예상 몫을 계산 (2026-11-25, 11-26 회차 창 기준으로 변경).

바꾼 점 (counterfactual_share_interim_20261122.py · mango_rank_table_20261125.py 대비)
- 환산 계수: 경로별 (우리 값 ÷ 같은 날 회차 공개값 평균)을 **동일 가중** 기하평균. 2-2회는 경로마다 같은 수로
  호출했으므로 2-1회 배분 비율을 가중치로 쓰지 않는다. 1% 미만 경로도 넣는다.
  TTFT = 초록(제공사 첫 조각) 기준 짧은 질문(S): OR 경로 generation latency 중앙값, Mango 첫 조각 중앙값.
  속도 = 긴 출력(G) 출력 속도 중앙값 ÷ 공개 p50 처리량.
- 후보 지표: 2-1회 마지막 스냅숏 한 장 대신, 2-2회 4회차 측정 시간 + 10분 안(환산 공개값과 같은 창,
  회차당 스냅숏 10개)의 공개 엔드포인트 스냅숏마다 후보 + Mango로 선택 확률을 계산한다(후보가 그 시각
  목록에 없으면 0). 회차별로 평균한 뒤 4회차 값을 산술평균한다. 2-1회 예측 몫과 같은 "확률의 평균"이다.
  후보 지표(TTFT · 속도 · 출력가 · 실효 입력가)는 후보였던 스냅숏 값의 회차별 중앙값 → 4회차 평균
  (환산 표의 공개값과 같은 방식). 2026-11-26: 기간 전체(224개) 기하평균에서 이 방식으로 바꿈.
- 그대로 둔 것: 2-1회 모델별 계수(S8_eff1h_split), 후보 = 2-1회에 한 번이라도 뽑힌 엔드포인트,
  Mango 정가 · 오류율 = 그 시각 후보 중앙값(입점 직후).
- Mango 캐시 적중률(2026-11-26 변경): 그 시각 후보들의 OR 공개 적중률을 2-1회 실제 몫으로 가중 평균
  (그 시각 목록에 있는 후보끼리 다시 나눔). 이전에는 2-1회 최다 선택 엔드포인트의 그 시각 값이라, 그 엔드포인트가
  목록에서 빠진 시각(GLM-5.3 Flash wafer/nvfp4, 07:39 ~ r3 전)에 0%가 들어갔다. --check는 이전 가정 그대로.
--check: 같은 코드로 2-1회 마지막 스냅숏 + 기존 Mango 값(variant_<vm>_greenS.json)을 넣어 기존 결과를 재현한다.

numpy 없이 돈다(스냅숏 읽기 함수는 analyze_exp2_1_choice_20261121.py에서 옮김).
사용(03_MangoBoost에서): python 08_도구/exp2_2/share_2_2_period_20261125.py [--check]
"""
import argparse
import ast
import bisect
import glob
import gzip
import json
import math
import os
import statistics as st
from collections import defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
D21 = os.path.join(ROOT, "90_작업자료", "실험_2-1회_20261120")
D22 = os.path.join(ROOT, "90_작업자료", "실험_2-2회_20261122")
FIN = os.path.join(D22, "최종분석_20261124")
OUT_JSON = os.path.join(FIN, "share_2_2_period_20261125.json")
OUT_TXT = os.path.join(FIN, "share_2_2_period_20261125.txt")
PUB22 = os.path.join(D22, "vm_결과_20261123", "seoul", "public")
PUB21 = os.path.join(D21, "vm_결과_20261122", "run_20261120")
PERIOD = (1795325400, 1795392600)          # r1 시작 2026-11-22 05:30 ~ r4 끝 2026-11-23 00:10 UTC
ROUNDS = {"r1": (1795325400, 1795327800), "r2": (1795347000, 1795349400),   # 회차 시작 ~ 끝(UTC)
          "r3": (1795368600, 1795371000), "r4": (1795390200, 1795392600)}
WIN_PAD = 600                               # 회차 끝 + 10분까지(환산 공개값 창과 같음)


def round_of(t):
    for r, (a, b) in ROUNDS.items():
        if a <= t <= b + WIN_PAD:
            return r
    return None
SPEC = "S8_eff1h_split"
MANGO = {"deepseek-v4.1-flash-1017": (0.20, 0.006, 0.80), "glm-5.3": (1.30, 0.14, 4.20),
         "glm-5.3-flash": (0.13, 0.03, 0.45), "kimi-k3.1-pro": (1.80, 0.19, 9.50)}   # 입력 · 캐시 입력 · 출력 $/100만


def paths():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner_20261122.py"), encoding="utf-8").read()
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "PATHS")
    return ast.literal_eval(node.value)


# ---------------------------------------------------------------- 공개 스냅숏 (analyze_exp2_1_choice_20261121.py와 같음)
def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_public(run):
    snaps = {k: defaultdict(list) for k in ("ep", "tool", "so", "cache")}
    for p in sorted(glob.glob(run + "/public/*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
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
                                      "pin": fnum(pr.get("prompt")), "pout": fnum(pr.get("completion")),
                                      "pcache": fnum(pr.get("input_cache_read")),
                                      "tag": x.get("provider_slug"), "name": x.get("provider_name")}
                    snaps["ep"][mk].append((t, d))
                elif api == "cache-hit-rate-comparison":
                    if body:
                        snaps["cache"][mk].append((t, {e: fnum(v) for e, v in body[-1]["y"].items()}))
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


# ---------------------------------------------------------------- 모형
def feats(pin, pc, pout, h, ttft, tps, terr, serr):
    ein = pin * (1 - h) + (pc if pc is not None else pin) * h
    return {"log_ein1h": math.log(ein * 1e6), "log_pout": math.log(pout * 1e6), "log_ttft": math.log(ttft),
            "log_tps": math.log(tps), "tool_err": terr, "so_err": serr}


def snapshot_shares(b, t, ep, cache, tool, so, ever, leader, mango_vals, cache_w=None):
    """한 시각의 후보 + Mango 선택 확률. mango_vals: vm -> (ttft, tps). 반환: (후보 지표, vm -> {e: p, 'mango': p})"""
    ch = asof(cache, t, 3600) or {}
    te = asof(tool, t, 3600) or {}
    se = asof(so, t, 3600) or {}
    cand = [e for e in ep if e in ever and ep[e]["ttft"] and ep[e]["tps"]]
    if not cand:
        return None
    rows, lv = {}, {}
    for e in cand:
        x = ep[e]
        h = min(max((ch.get(e) or 0.0) / 100, 0), 1)
        pin, pout = x["pin"] or 1e-10, x["pout"] or 1e-10
        f = feats(pin, x["pcache"], pout, h, x["ttft"], x["tps"], te.get(e) or 0.0, se.get(e) or 0.0)
        rows[e] = sum(b[k] * f[k] for k in b)
        lv[e] = {"ttft": x["ttft"], "tps": x["tps"], "pout": pout * 1e6, "ein": math.exp(f["log_ein1h"]),
                 "name": x["name"], "tag": x["tag"]}
    if cache_w is None:     # 이전 가정(--check): 2-1회 최다 선택 엔드포인트의 그 시각 값
        h_lead = min(max((ch.get(leader) or 0.0) / 100, 0), 1)
    else:                   # 그 시각 후보들의 적중률을 2-1회 실제 몫으로 가중 평균(목록에 있는 후보끼리 다시 나눔)
        hs = [(min(max((ch.get(e) or 0.0) / 100, 0), 1), cache_w[e]) for e in cand]
        h_lead = sum(h * w for h, w in hs) / sum(w for _, w in hs)
    med_t = st.median((te.get(e) or 0.0) for e in cand)
    med_s = st.median((se.get(e) or 0.0) for e in cand)
    out = {}
    for vm, (mt, mtps) in mango_vals.items():
        m_in, m_c, m_out = MANGO_CUR
        fm = feats(m_in / 1e6, m_c / 1e6, m_out / 1e6, h_lead, mt, mtps, med_t, med_s)
        um = sum(b[k] * fm[k] for k in b)
        mx = max(list(rows.values()) + [um])
        z = sum(math.exp(u - mx) for u in rows.values()) + math.exp(um - mx)
        p = {e: math.exp(u - mx) / z for e, u in rows.items()}
        p["mango"] = math.exp(um - mx) / z
        out[vm] = {"p": p, "mango_ein": math.exp(fm["log_ein1h"]), "u_mango": um, "u": dict(rows)}
    return lv, out


# ---------------------------------------------------------------- 환산 (2-2회만, 동일 가중)
def egeo(vals):
    v = [x for x in vals if x and x > 0]
    return math.exp(sum(math.log(x) for x in v) / len(v)) if v else None


def conversion(P):
    refs = json.load(open(os.path.join(FIN, "refs_public_local_20261124.json"), encoding="utf-8"))["ep"]
    green = json.load(open(os.path.join(FIN, "green_factor_20261125.json"), encoding="utf-8"))
    res = {}
    for vm in ("seoul", "iad"):
        summ = json.load(open(os.path.join(FIN, f"summary_{vm}_local_20261124.json"), encoding="utf-8"))["models"]
        for mk in P:
            g = green[vm][mk]["greenS"]
            fac_t = {t: v for t, v in g["factor_paths"].items() if v}
            fac_s = {}
            for t in P[mk]:
                ours = summ[mk]["paths"].get(t, {}).get("G_tps")
                pub = [d["tps"] for d in (refs.get(f"{mk}|{t}") or {}).values() if d.get("tps")]
                if ours and pub:
                    fac_s[t] = ours / st.mean(pub)
            Ft, Fs = egeo(fac_t.values()), egeo(fac_s.values())
            m_green = g["mango_green_ms"]
            m_tps = summ[mk]["paths"]["mango"]["G_tps"]
            res.setdefault(vm, {})[mk] = {
                "ttft_factor": Ft, "ttft_factor_paths": fac_t, "tps_factor": Fs, "tps_factor_paths": fac_s,
                "mango_green_ms": m_green, "mango_G_tps": m_tps,
                "mango_ttft_or": m_green / Ft, "mango_tps_or": m_tps / Fs,
                "ttft_or_range_by_path": [m_green / max(fac_t.values()), m_green / min(fac_t.values())],
                "tps_or_range_by_path": [m_tps / max(fac_s.values()), m_tps / min(fac_s.values())],
                "old_weighted": {"ttft_factor": g["factor"], "mango_ttft_or": g["mango_ttft_or_scale"],
                                 "tps_factor": summ[mk]["calib"]["tps_factor"],
                                 "mango_tps_or": summ[mk]["calib"]["mango_tps_or_scale"]}}
    return res


# ---------------------------------------------------------------- 실행
MANGO_CUR = None


def run_period(snaps, coef, dec, P, conv, period, last_only=False, mango_override=None):
    global MANGO_CUR
    out = {}
    for mk in P:
        MANGO_CUR = MANGO[mk]
        b = dict(zip(coef[mk][SPEC]["feats"], coef[mk][SPEC]["coef_raw"]))
        eps = dec["models"][mk]["endpoints"]
        N = dec["models"][mk]["n_choices"]
        cnt = {e["endpoint"]: e["obs_share"] * N for e in eps}
        ever = {e for e, c in cnt.items() if c > 0}
        leader = max(ever, key=lambda e: cnt[e])
        series = snaps["ep"][mk]
        if last_only:
            series = series[-1:]
        else:
            series = [z for z in series if round_of(z[0])]
        mv = mango_override[mk] if mango_override else {vm: (conv[vm][mk]["mango_ttft_or"], conv[vm][mk]["mango_tps_or"])
                                                         for vm in conv}
        acc = {vm: defaultdict(lambda: defaultdict(float)) for vm in mv}     # vm -> 회차 -> e -> 확률 합
        lvacc = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # e -> 지표 -> 회차 -> 값
        nr = defaultdict(int)
        names, n_snap, ncand = {}, 0, []
        for t, ep in series:
            r = snapshot_shares(b, t, ep, snaps["cache"][mk], snaps["tool"][mk], snaps["so"][mk], ever, leader, mv,
                                cache_w=None if last_only else cnt)
            if r is None:
                continue
            lv, by = r
            rd = "last" if last_only else round_of(t)
            n_snap += 1
            nr[rd] += 1
            ncand.append(len(lv))
            for e, x in lv.items():
                names[e] = f'{x["name"]} ({x["tag"]})'
                for k in ("ttft", "tps", "pout", "ein"):
                    lvacc[e][k][rd].append(x[k])
            for vm, o in by.items():
                for e, p in o["p"].items():
                    acc[vm][rd][e] += p
                lvacc["mango|" + vm]["ein"][rd].append(o["mango_ein"])
        res = {"n_snapshots": n_snap, "t_first": series[0][0] if series else None, "t_last": series[-1][0] if series else None,
               "n_candidates_mean": st.mean(ncand) if ncand else None, "n_candidates_union": len(names),
               "n_snapshots_by_round": dict(nr), "leader": names.get(leader, leader), "by_vm": {}}

        def rmean(by_round):        # 회차별 중앙값 → 회차 평균
            return st.mean(st.median(v) for v in by_round.values() if v)

        # 기간 지표(그 엔드포인트가 후보였던 스냅숏만, 회차별 중앙값 → 4회차 평균)와 순위
        geo = {e: {k: rmean(rr) for k, rr in d.items()} for e, d in lvacc.items() if not e.startswith("mango|")}
        for vm in mv:
            allE = set().union(*[set(x) for x in acc[vm].values()])
            share = {e: st.mean(acc[vm][r].get(e, 0.0) / nr[r] for r in nr) for e in allE}
            mt, mtps = mv[vm]
            m_ein = rmean(lvacc["mango|" + vm]["ein"])
            m_lv = {"ttft": mt, "tps": mtps, "pout": MANGO[mk][2], "ein": m_ein}

            def rank(key, val, low_better):
                others = [g[key] for g in geo.values()]
                return 1 + sum(1 for o in others if (o < val if low_better else o > val))

            cands = sorted(((e, s) for e, s in share.items() if e != "mango"), key=lambda z: -z[1])
            m_rank = 1 + sum(1 for _, s in cands if s > share["mango"])
            res["by_vm"][vm] = {
                "mango_ttft_or": mt, "mango_tps_or": mtps, "mango_share": share["mango"], "mango_share_rank": m_rank,
                "mango_metric_rank": {"ttft": rank("ttft", mt, True), "tps": rank("tps", mtps, False),
                                      "pout": rank("pout", m_lv["pout"], True), "ein": rank("ein", m_ein, True)},
                "mango_level": m_lv,
                "candidates": [{"endpoint": e, "name": names.get(e, e), "share": s, "obs_share_2_1": cnt.get(e, 0) / N,
                                "level": geo.get(e),
                                "rank": {"ttft": 1 + sum(1 for g in geo.values() if g["ttft"] < geo[e]["ttft"]),
                                         "tps": 1 + sum(1 for g in geo.values() if g["tps"] > geo[e]["tps"]),
                                         "pout": 1 + sum(1 for g in geo.values() if g["pout"] < geo[e]["pout"]),
                                         "ein": 1 + sum(1 for g in geo.values() if g["ein"] < geo[e]["ein"])}}
                               for e, s in cands]}
        out[mk] = res
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="2-1회 마지막 스냅숏 + 기존 Mango 값으로 기존 결과 재현")
    a = ap.parse_args()
    P = paths()
    coef = json.load(open(os.path.join(D21, "최종분석_20261122", "refit_by_model_20261123.json"), encoding="utf-8"))["eligible"]["by_model"]
    dec = json.load(open(os.path.join(D21, "최종분석_20261122", "decompose_endpoint_20261124.json"), encoding="utf-8"))
    if a.check:
        old = {vm: json.load(open(os.path.join(FIN, f"variant_{vm}_greenS.json"), encoding="utf-8"))["models"] for vm in ("seoul", "iad")}
        mo = {mk: {vm: (old[vm][mk]["calib"]["mango_ttft_or_scale"], old[vm][mk]["calib"]["mango_tps_or_scale"]) for vm in old}
              for mk in P}
        r = run_period(load_public(PUB21), coef, dec, P, None, None, last_only=True, mango_override=mo)
        for mk, x in r.items():
            print(mk, {vm: f'{v["mango_share"]:.2%} · {v["mango_share_rank"]}위' for vm, v in x["by_vm"].items()})
        return
    conv = conversion(P)
    snaps = load_public(PUB22)
    res = run_period(snaps, coef, dec, P, conv, PERIOD)
    json.dump({"conversion": conv, "share": res, "period_utc": PERIOD, "rounds_utc": ROUNDS, "round_pad_s": WIN_PAD}, open(OUT_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    lines = []
    for mk in P:
        x = res[mk]
        lines.append(f"{mk}: 스냅숏 {x['n_snapshots']}개, 후보 평균 {x['n_candidates_mean']:.1f}곳(합집합 {x['n_candidates_union']})")
        for vm in ("seoul", "iad"):
            c, v = conv[vm][mk], x["by_vm"][vm]
            o = c["old_weighted"]
            lines.append(f"  [{vm}] 환산 TTFT F={c['ttft_factor']:.2f}(기존 {o['ttft_factor']:.2f}) → {c['mango_ttft_or']:.0f}ms(기존 {o['mango_ttft_or']:.0f}), "
                         f"속도 F={c['tps_factor']:.2f}(기존 {o['tps_factor']:.2f}) → {c['mango_tps_or']:.0f}(기존 {o['mango_tps_or']:.0f}) | "
                         f"몫 {v['mango_share']:.2%} · {v['mango_share_rank']}위 | 지표 순위 TTFT {v['mango_metric_rank']['ttft']} · "
                         f"속도 {v['mango_metric_rank']['tps']} · 출력가 {v['mango_metric_rank']['pout']} · 입력가 {v['mango_metric_rank']['ein']}")
    open(OUT_TXT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
