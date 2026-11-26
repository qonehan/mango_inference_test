#!/usr/bin/env python3
"""실험 2-2회 OR 고정 제공사 선정용 집계 (2026-11-22).

선정에 필요한 값만 계산한다(로짓·부트스트랩 없음).
  1. 엔드포인트별 실제 배정 비율: 2-1회 기본 라우팅 호출(label=main, 성공)의 첫 시도 엔드포인트.
  2. 공개 지표 기간 대표값(중앙값): stats/endpoint의 TTFT p50·처리 속도 p50·게시 단가,
     effective-pricing의 실효 입력 단가. 전체 기간과 최근 6시간 두 가지.
  3. TTFT 1위, 긴 입력(입력 3,000·출력 16토큰) 기준 요청당 실효 비용 1위.
     후보: 최근 6시간 stats/endpoint에 TTFT가 있고 deranked가 아니며 출력 단가가 0이 아닌 엔드포인트.

사용(VM): /opt/mango-bench/anvenv/bin/python select_fixed_providers_20261122.py --run run_20261120 --out sel.json
analyze_exp2_1_choice_20261121.py와 같은 폴더에 두고 실행한다(읽기 함수 재사용).
"""
import argparse
import json
import statistics as st
from collections import Counter, defaultdict

import analyze_exp2_1_choice_20261121 as A

L_IN, L_OUT = 3000, 16
RECENT_S = 6 * 3600


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    reqs = A.load_requests(a.run)
    snaps = A.load_public(a.run)
    t0, t1 = reqs[0]["t"], reqs[-1]["t"]
    out = {"window_utc": [t0, t1], "n_requests": len(reqs), "models": {}}
    for mk in A.MODELS:
        cnt = Counter(q["chosen"] for q in reqs if q["mk"] == mk)
        n = sum(cnt.values())
        vals = defaultdict(lambda: defaultdict(list))   # e -> field -> [(t, v)]
        info = {}
        for t, ep in snaps["ep"][mk]:
            for e, x in ep.items():
                info[e] = {"tag": x["tag"], "name": x["name"], "quant": x["quant"]}
                for f in ("ttft", "tps", "pin", "pout", "pcache"):
                    vals[e][f].append((t, x[f]))
                vals[e]["deranked"].append((t, x["deranked"]))
        for t, ef in snaps["eff"][mk]:
            for e, x in ef.items():
                vals[e]["ein"].append((t, x["ein"]))
        rows = []
        for e in set(cnt) | set(info):
            v = vals[e]
            r = {"e": e, **info.get(e, {"tag": None, "name": None, "quant": None}),
                 "n": cnt.get(e, 0), "share": cnt.get(e, 0) / n if n else 0.0}
            for f in ("ttft", "tps", "pin", "pout", "pcache", "ein"):
                r[f] = med([x for _, x in v[f]])
                r[f + "_6h"] = med([x for t, x in v[f] if t >= t1 - RECENT_S])
            rec_d = [x for t, x in v["deranked"] if t >= t1 - RECENT_S]
            r["deranked_6h"] = bool(rec_d) and all(rec_d)
            # 단위: effective-pricing의 ein은 100만 토큰당 $, stats/endpoint의 pin·pout은 토큰당 $
            ein = r["ein_6h"] / 1e6 if r["ein_6h"] is not None else r["pin_6h"]
            r["cost_L_6h"] = (ein * L_IN + r["pout_6h"] * L_OUT) if (ein is not None and r["pout_6h"]) else None
            rows.append(r)
        rows.sort(key=lambda r: -r["share"])
        cand = [r for r in rows if r["ttft_6h"] and not r["deranked_6h"] and r["pout_6h"]]
        top5 = [r["tag"] for r in rows[:5]]
        t1st = min(cand, key=lambda r: r["ttft_6h"]) if cand else None
        c1st = min((r for r in cand if r["cost_L_6h"]), key=lambda r: r["cost_L_6h"], default=None)
        out["models"][mk] = {"n": n, "n_endpoints_listed": len(info), "n_candidates": len(cand),
                             "top5": top5, "top5_share": sum(r["share"] for r in rows[:5]),
                             "ttft_1st": t1st and t1st["tag"], "cost_L_1st": c1st and c1st["tag"],
                             "rows": rows}
    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1)
    # 요약 출력
    for mk, m in out["models"].items():
        print(f"== {mk} n={m['n']} 목록 {m['n_endpoints_listed']} 후보 {m['n_candidates']} 상위5합 {m['top5_share']:.3f}")
        for r in m["rows"][:8]:
            f = lambda x, k=1: "-" if x is None else round(x, k)
            print(f"  {str(r['tag']):28s} {r['share']*100:5.1f}%  ttft6h {f(r['ttft_6h'],0)}  tps6h {f(r['tps_6h'])}"
                  f"  ein6h {f(r['ein_6h'] and r['ein_6h']*1e6,4)}  pout {f(r['pout_6h'] and r['pout_6h']*1e6,3)}")
        for k in ("ttft_1st", "cost_L_1st"):
            r = next((x for x in m["rows"] if x["tag"] == m[k]), None)
            if r:
                print(f"  {k}: {r['tag']} share {r['share']*100:.1f}% ttft6h {r['ttft_6h']} costL {r['cost_L_6h'] and r['cost_L_6h']*1e6:.1f}u$")


if __name__ == "__main__":
    main()
