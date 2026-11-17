"""사전 등록 분석 (프로토콜 v0.9.4 2·9절). 회차 폴더들을 합쳐 주 비교 판정표와 기본 배분을 만든다.

주 비교 (모델마다 Mango ÷ OR-기본, 주 비교 블록만 사용):
  P1 W2 세션값(2~10턴 TTFT content 기하평균) 짝 기하평균 비율   작을수록 좋음
  P2 공통 W1 decode 처리량 짝 기하평균 비율                      클수록 좋음
  P3 W1·W3·W2 성공률 차이 1/3 가중, 보수적 이항 구간 (추정만)
  P4 W2 성공 세션당 비용 비율 (실패 비용 포함, Mango는 단가 추정)  작을수록 좋음
구간: 회차 층화 짝 부트스트랩 10,000회, 시드 20261116, 99.6875%. 판정: 0.90~1.10 동등 범위.
배분(RQ3): OR-기본 W1·W3 최종 응답 엔드포인트·제공사 몫, 요청 건수·토큰 기준. 회차별 몫과
  회차 → 요청 2단계 부트스트랩 95% 구간.
파일럿: --pilot이면 모델별 W2 세션 로그 비율 표준편차 s와 n = ceil((2.96 s/0.05)^2) (4의 배수, 32~48).

사용: python analyze.py runs/2026-11-18/S1 runs/2026-11-18/S2 ... --out results/analysis
      python analyze.py runs/pilot --pilot
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from analysis_metrics import cost_ratio, success_difference
from check_slot import mango_cost

ALPHA = 0.05 / 16
SEED = 20261116
REPS = 10000


def load(folders: list[Path]) -> tuple[list, dict, dict]:
    recs, gens, prices = [], {}, {}
    for d in folders:
        for l in (d / "raw.jsonl").read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                if r.get("task") != "WARMUP":
                    r["_slot_dir"] = d.name
                    recs.append(r)
        if (d / "generations.jsonl").exists():
            for l in (d / "generations.jsonl").read_text(encoding="utf-8").splitlines():
                g = json.loads(l)
                gens[g["request_id"]] = g
        if (d / "mango_models.json").exists():
            prices[d.name] = {m["id"]: m.get("cost") for m in
                              json.loads((d / "mango_models.json").read_text(encoding="utf-8"))["data"]}
    return recs, gens, prices


def quantile(v: list[float], q: float) -> float:
    x = q * (len(v) - 1)
    i = int(x)
    return v[i] + (v[min(i + 1, len(v) - 1)] - v[i]) * (x - i)


def strat_boot(strata: dict, stat, alpha: float, reps=REPS, seed=SEED):
    """strata: {slot: [unit,...]}. 회차 안에서 단위를 복원추출. 정의 안 되는 재표본이 있으면 None."""
    rng = random.Random(seed)
    groups = [g for g in strata.values() if g]
    vals = []
    for _ in range(reps):
        sample = [rng.choice(g) for g in groups for _ in g]
        v = stat(sample)
        if v is None or not math.isfinite(v):
            return None
        vals.append(v)
    vals.sort()
    return quantile(vals, alpha / 2), quantile(vals, 1 - alpha / 2)


def geo_ratio(pairs):
    return math.exp(sum(math.log(m / o) for m, o in pairs) / len(pairs)) if pairs else None


def verdict(ci, smaller_better: bool) -> str:
    if ci is None:
        return "판정 불가(구간 없음)"
    lo, hi = ci
    good, bad = (hi < 0.90, lo > 1.10) if smaller_better else (lo > 1.10, hi < 0.90)
    if good:
        return "Mango 우위"
    if bad:
        return "Mango 열위"
    if 0.90 <= lo and hi <= 1.10:
        return "동등"
    return "판정 불가"


def primary(recs, gens, prices, mk) -> dict:
    R = [r for r in recs if r["model_key"] == mk and r.get("primary")]
    by_block = defaultdict(dict)
    for r in R:
        if r["task"] in ("W1", "W3"):
            by_block[r["block_id"]][r["arm"]] = r
    sess = defaultdict(lambda: defaultdict(list))  # block -> arm -> turns
    for r in R:
        if r["task"] == "W2":
            sess[r["block_id"]][r["arm"]].append(r)
    slot_of = {r["block_id"]: r["slot"] for r in R}
    out = {"model": mk}

    # P2: 공통 W1 decode 처리량
    p2 = defaultdict(list)
    for b, arms in by_block.items():
        m, o = arms.get("M"), arms.get("OR-default")
        if m and o and m["task"] == "W1" and m.get("decode_tps") and o.get("decode_tps"):
            p2[slot_of[b]].append((m["decode_tps"], o["decode_tps"]))
    flat = [p for v in p2.values() for p in v]
    out["P2"] = {"n_pairs": len(flat), "ratio": geo_ratio(flat),
                 "ci": strat_boot(p2, geo_ratio, ALPHA) if flat else None}
    out["P2"]["verdict"] = verdict(out["P2"]["ci"], smaller_better=False)

    # 세션 요약
    def summarize(turns):
        turns = sorted(turns, key=lambda t: t["turn"])
        measured = [t for t in turns if t.get("status_note") != "unmeasured"]
        ok = len(turns) == 10 and all(t.get("ok") for t in turns)
        ttft = [t["ttft_content_ms"] for t in turns if t["turn"] >= 2 and t.get("ttft_content_ms")]
        value = math.exp(sum(map(math.log, ttft)) / len(ttft)) if ok and len(ttft) == 9 else None
        ours = any(t.get("fail_side") == "ours" for t in turns)
        return {"ok": ok, "value": value, "ours": ours, "unmeasured": len(measured) < len(turns), "turns": turns}

    p1, p4, w2bin = defaultdict(list), defaultdict(list), []
    p4_unknown = False
    for b, arms in sess.items():
        if "M" not in arms or "OR-default" not in arms:
            continue
        m, o = summarize(arms["M"]), summarize(arms["OR-default"])
        if m["ours"] or o["ours"] or m["unmeasured"] or o["unmeasured"]:
            continue
        w2bin.append((int(m["ok"]), int(o["ok"])))
        if m["value"] and o["value"]:
            p1[slot_of[b]].append((m["value"], o["value"]))
        mc = [mango_cost(t, prices.get(t["_slot_dir"], {})) for t in m["turns"] if t.get("prompt_tokens") is not None]
        oc = [(gens.get(t["request_id"]) or {}).get("total_cost") for t in o["turns"] if t.get("resp_id")]
        if any(c is None for c in mc + oc):
            p4_unknown = True
        p4[slot_of[b]].append(((sum(c or 0 for c in mc), m["ok"]), (sum(c or 0 for c in oc), o["ok"])))
    flat = [p for v in p1.values() for p in v]
    out["P1"] = {"n_pairs": len(flat), "ratio": geo_ratio(flat),
                 "ci": strat_boot(p1, geo_ratio, ALPHA) if flat else None}
    out["P1"]["verdict"] = verdict(out["P1"]["ci"], smaller_better=True)
    flat4 = [p for v in p4.values() for p in v]
    if p4_unknown or not flat4:
        out["P4"] = {"n_sessions": len(flat4), "ratio": None, "ci": None, "verdict": "보류(비용 미확인)"}
    else:
        out["P4"] = {"n_sessions": len(flat4), "ratio": cost_ratio(flat4), "ci": strat_boot(p4, cost_ratio, ALPHA)}
        out["P4"]["verdict"] = verdict(out["P4"]["ci"], smaller_better=True)
    out["P4"]["note"] = "Mango 비용은 단가 추정. 콘솔 정산 대조 전에는 확정하지 않음"

    # P3: 성공률 차이 (우리 쪽 실패·미측정 제외)
    def binpairs(task):
        res = []
        for b, arms in by_block.items():
            m, o = arms.get("M"), arms.get("OR-default")
            if m and o and m["task"] == task and "ours" not in (m.get("fail_side"), o.get("fail_side")) \
                    and "unmeasured" not in (m.get("status_note"), o.get("status_note")):
                res.append((int(bool(m.get("ok"))), int(bool(o.get("ok")))))
        return res
    groups = [binpairs("W1"), binpairs("W3"), w2bin]
    try:
        pt, lo, hi = success_difference(groups, ALPHA)
        out["P3"] = {"n": [len(g) for g in groups], "diff_pp": round(pt * 100, 2),
                     "ci_pp": (round(lo * 100, 2), round(hi * 100, 2)), "verdict": "추정만(판정 안 함)"}
    except ValueError as e:
        out["P3"] = {"n": [len(g) for g in groups], "verdict": f"추정 불가: {e}"}
    return out


def routing(recs, gens, mk) -> list[dict]:
    R = [r for r in recs if r["model_key"] == mk and r["arm"] == "OR-default" and r["task"] in ("W1", "W3")
         and r.get("ok") and r["request_id"] in gens]
    by_slot = defaultdict(lambda: defaultdict(list))
    for r in R:
        g = gens[r["request_id"]]
        tokens = (r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)
        by_slot[r["slot"]][r["task"]].append({"endpoint": g.get("final_endpoint"), "provider": g.get("provider_name"),
                                              "tokens": tokens})
    rows = []
    for level in ("endpoint", "provider"):
        cats = sorted({x[level] for s in by_slot.values() for t in s.values() for x in t if x[level]})
        for cat in cats:
            def share(sample, by_tokens):
                tot = sum(x["tokens"] if by_tokens else 1 for x in sample)
                return sum((x["tokens"] if by_tokens else 1) for x in sample if x[level] == cat) / tot if tot else None
            for by_tokens in (False, True):
                per_slot = {s: share([x for t in v.values() for x in t], by_tokens) for s, v in sorted(by_slot.items())}
                rng = random.Random(SEED)
                slots = list(by_slot)
                vals = []
                for _ in range(REPS):
                    sample = []
                    for s in (rng.choice(slots) for _ in slots):  # ① 회차 복원추출
                        for t in by_slot[s].values():                # ② 작업별 요청 복원추출
                            sample += [rng.choice(t) for _ in t]
                    v = share(sample, by_tokens)
                    if v is not None:
                        vals.append(v)
                vals.sort()
                allx = [x for v in by_slot.values() for t in v.values() for x in t]
                rows.append({"model": mk, "level": level, "category": cat, "basis": "tokens" if by_tokens else "requests",
                             "share": share(allx, by_tokens), "per_slot": per_slot,
                             "ci95": (quantile(vals, 0.025), quantile(vals, 0.975)) if vals else None,
                             "n_requests": len(allx), "n_slots": len(slots)})
    return rows


def pilot_n(recs, mk) -> dict:
    sess = defaultdict(lambda: defaultdict(list))
    for r in recs:
        if r["model_key"] == mk and r["task"] == "W2" and r["arm"] in ("M", "OR-default") and r.get("block_id"):
            sess[r["block_id"]][r["arm"]].append(r)
    logs = []
    for arms in sess.values():
        vals = []
        for a in ("M", "OR-default"):
            t = [x["ttft_content_ms"] for x in arms.get(a, []) if x["turn"] >= 2 and x.get("ok") and x.get("ttft_content_ms")]
            vals.append(math.exp(sum(map(math.log, t)) / len(t)) if len(t) == 9 else None)
        if all(vals):
            logs.append(math.log(vals[0] / vals[1]))
    if len(logs) < 2:
        return {"model": mk, "sessions": len(logs), "s": None, "n": None}
    mu = sum(logs) / len(logs)
    s = math.sqrt(sum((x - mu) ** 2 for x in logs) / (len(logs) - 1))
    raw = math.ceil((2.96 * s / 0.05) ** 2)
    n = min(48, max(32, math.ceil(raw / 4) * 4))
    return {"model": mk, "sessions": len(logs), "s": round(s, 4), "n_raw": raw, "n": n,
            "note": "48로 제한됨: 판정 불가 가능성 기록" if raw > 48 else ""}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--out", default="analysis")
    ap.add_argument("--pilot", action="store_true")
    a = ap.parse_args()
    recs, gens, prices = load([Path(f) for f in a.folders])
    models = sorted({r["model_key"] for r in recs})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.pilot:
        res = [pilot_n(recs, mk) for mk in models]
        (out / "pilot_w2_n.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return
    prim = [primary(recs, gens, prices, mk) for mk in models]
    (out / "primary.json").write_text(json.dumps(prim, indent=2, ensure_ascii=False), encoding="utf-8")
    rows = [r for mk in models for r in routing(recs, gens, mk)]
    with open(out / "routing.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["model"])
        w.writeheader()
        for r in rows:
            w.writerow({**r, "per_slot": json.dumps(r["per_slot"]), "ci95": json.dumps(r["ci95"])})
    for p in prim:
        print(p["model"], {k: (p[k].get("ratio") or p[k].get("diff_pp"), p[k]["verdict"]) for k in ("P1", "P2", "P3", "P4")})


if __name__ == "__main__":
    main()
