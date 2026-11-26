#!/usr/bin/env python3
"""2-1회: 엔드포인트마다 "왜 많이(적게) 뽑혔나"를 지표별 기여로 나눈다 (2026-11-24).

결과 문서: 02_분석/32_실험2-1회_최종_2-2회_결과정리_20261123.md 2-1절

- 계수: 모델별 6변수 재추정(refit_by_model_20261123.json, S8_eff1h_split). 원 단위.
- 기여: 선택 1건마다 후보 i의 지표 k 기여 = β_k × (x_ik − 그 선택의 후보 평균 x_k).
  합은 "후보 평균에 비해 효용이 얼마나 높은가"이고, exp(기여)는 평균 후보 대비 끌림 배수다.
  후보로 있었던 선택들에 걸쳐 평균한다.
- 실제 몫(뽑힌 비율)과 모형 예측 몫(예측 확률 평균)을 같이 둔다. log(실제 ÷ 예측)은 지표로 설명되지 않는 부분이다
  (용량·내부 규칙·탐색 트래픽 등, 원인은 이 자료로 구분할 수 없음).
사용: python decompose_endpoint_20261124.py --run <run_20261120> --coef <refit_by_model_20261123.json> --out <json>
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import analyze_exp2_1_choice_20261121 as A  # noqa: E402

FEATS6 = ["log_ein1h", "log_pout", "log_ttft", "log_tps", "tool_err", "so_err"]
RAW = {"log_ein1h": "ein_usd_m", "log_pout": "pout_usd_m", "log_ttft": "ttft_ms", "log_tps": "tps"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--coef", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-share", type=float, default=0.01)
    a = ap.parse_args()
    coef = json.load(open(a.coef, encoding="utf-8"))["eligible"]["by_model"]
    snaps = A.load_public(a.run)
    reqs = A.load_requests(a.run)
    ch, drop = A.build(reqs, snaps, eligible_only=True)
    print("choices", len(ch), dict(drop))
    out = {"feats": FEATS6, "models": {}}
    for mk in A.MODELS:
        r = coef[mk]["S8_eff1h_split"]
        b = np.array([dict(zip(r["feats"], r["coef_raw"]))[f] for f in FEATS6])
        names = {}
        for _, d in snaps["ep"][mk]:
            for e, x in d.items():
                names[e] = f'{x.get("name")} ({x.get("tag")})'
        acc = defaultdict(lambda: {"n": 0, "chosen": 0, "p": 0.0, "contrib": np.zeros(len(FEATS6)), "raw": np.zeros(len(FEATS6))})
        cm = [c for c in ch if c["mk"] == mk]
        for c in cm:
            X = np.array([[row[f] for f in FEATS6] for row in c["rows"]])
            U = X @ b
            P = np.exp(U - U.max())
            P /= P.sum()
            dev = (X - X.mean(0)) * b
            for k, row in enumerate(c["rows"]):
                s = acc[row["e"]]
                s["n"] += 1
                s["chosen"] += row["e"] == c["chosen"]
                s["p"] += P[k]
                s["contrib"] += dev[k]
                s["raw"] += X[k]
        N = len(cm)
        rows = []
        for e, s in acc.items():
            obs, pred = s["chosen"] / N, s["p"] / N
            con = s["contrib"] / s["n"]
            raw = s["raw"] / s["n"]
            rows.append({
                "endpoint": e, "name": names.get(e, e), "present": s["n"] / N, "obs_share": obs, "pred_share": pred,
                "contrib": dict(zip(FEATS6, con.tolist())), "total": float(con.sum()),
                "unexplained": math.log(obs / pred) if obs > 0 and pred > 0 else None,
                "level": {RAW.get(f, f): (math.exp(v) if f in RAW else v) for f, v in zip(FEATS6, raw.tolist())},
            })
        rows.sort(key=lambda x: -x["obs_share"])
        out["models"][mk] = {"n_choices": N, "endpoints": rows}
        print(f"\n== {mk}  선택 {N}건")
        print(f"{'엔드포인트':38} {'실제':>6} {'예측':>6} | " + " ".join(f"{f[4:] if f.startswith('log_') else f:>8}" for f in FEATS6)
              + f" {'합':>6} {'설명안됨':>7} | 출력가 TTFT 속도")
        for x in rows:
            if x["obs_share"] < a.min_share and x["pred_share"] < a.min_share:
                continue
            lv = x["level"]
            ue = f'{math.exp(x["unexplained"]):6.2f}x' if x["unexplained"] is not None else "     –"
            print(f'{x["name"][:38]:38} {x["obs_share"]:6.1%} {x["pred_share"]:6.1%} | '
                  + " ".join(f'{math.exp(x["contrib"][f]):7.2f}x' for f in FEATS6)
                  + f' {math.exp(x["total"]):5.1f}x {ue} | ${lv["pout_usd_m"]:.2f} {lv["ttft_ms"]:.0f}ms {lv["tps"]:.0f}')
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
