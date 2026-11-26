#!/usr/bin/env python3
"""반사실 몫 시나리오: 초록(제공사 첫 조각) 기준 TTFT 환산 (2026-11-25).

배경: OR 공개 TTFT(stats/endpoint p50_latency, latency_metric="latency")는 경로 간 차이가 generation `latency`
(= Broadcast 제공사 첫 토큰, 라우터·추론→본문 제외)와 가장 잘 맞았다(모델·VM·회차 평균 제거 후 log 상관 0.69,
라우터 포함 0.62, 본문 0.34). 그래서 OR 척도와 같은 구간끼리 환산한다.

  OR 경로 t의 초록 = 그 경로 요청들의 generation latency 중앙값 (VM별, 요청 종류별)
  Mango 초록       = Mango ttft_any_ms 중앙값 (직접 호출이라 OR 경유분이 없음)
  환산 계수 F      = 배분 가중 기하평균_t [ 경로 t 초록 ÷ 회차 공개 p50 평균 ]  (recompute_local_20261124.factor와 같은 식)
  Mango OR 척도    = Mango 초록 ÷ F
  greenS: 짧은 요청(S, 입력 약 60토큰) 기준, greenL: 긴 입력(L, 약 3,100토큰) 기준.
속도 환산과 나머지(가격·후보)는 기본(variant_<vm>_base.json)과 같다.
사용: python make_variant_green_20261125.py --vmdir <vm_결과_20261123> --dir <최종분석_20261124>
"""
import argparse
import ast
import copy
import json
import math
import os
import statistics
import sys
import types
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "runner_20261122.py"), encoding="utf-8").read()
node = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "PATHS")
_mod = types.ModuleType("runner_20261122")
_mod.PATHS = ast.literal_eval(node.value)
sys.modules["runner_20261122"] = _mod
sys.path.insert(0, HERE)
import analyze_interim_20261122 as AI  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--vmdir", required=True)
ap.add_argument("--dir", required=True)
a = ap.parse_args()
pub = json.load(open(os.path.join(a.dir, "refs_public_local_20261124.json"), encoding="utf-8"))["ep"]


def wgeo(vals, w):
    num = sum(w[t] * math.log(v) for t, v in vals.items() if v and t in w)
    den = sum(w[t] for t, v in vals.items() if v and t in w)
    return math.exp(num / den) if den else None


report = {}
for vm in ("seoul", "iad"):
    run = os.path.join(a.vmdir, vm, "run")
    lat = {}
    for line in open(os.path.join(run, "generations.jsonl"), encoding="utf-8"):
        try:
            g = json.loads(line)
        except ValueError:
            continue
        L = (g.get("data") or {}).get("latency")
        if g.get("resp_id") and L is not None:
            lat[g["resp_id"]] = float(L)
    rows = [r for r in AI.load(run) if r.get("label") == "main" and r.get("ok") and r.get("ttft_any_ms")]
    green = defaultdict(list)
    for r in rows:
        if r.get("kind") not in ("S", "L"):
            continue
        if r["host"] == "mango":
            green[(r["mk"], "mango", r["kind"])].append(r["ttft_any_ms"])
        elif r.get("resp_id") in lat:
            green[(r["mk"], r["tag"], r["kind"])].append(lat[r["resp_id"]])
    base = json.load(open(os.path.join(a.dir, f"variant_{vm}_base.json"), encoding="utf-8"))
    for kind in ("S", "L"):
        v = copy.deepcopy(base)
        for mk, m in v["models"].items():
            fac = {}
            for t in AI.PATHS[mk]:
                xs = green.get((mk, t, kind))
                pv = [d["ttft"] for d in (pub.get(f"{mk}|{t}") or {}).values() if d.get("ttft")]
                if xs and pv:
                    fac[t] = statistics.median(xs) / statistics.mean(pv)
            F = wgeo(fac, AI.base_w(mk))
            mg = statistics.median(green[(mk, "mango", kind)])
            m["calib"]["mango_ttft_or_scale"] = mg / F
            report.setdefault(vm, {}).setdefault(mk, {})[f"green{kind}"] = {
                "factor": F, "factor_paths": fac, "mango_green_ms": mg, "mango_ttft_or_scale": mg / F,
                "path_green_ms": {t: statistics.median(green[(mk, t, kind)]) for t in AI.PATHS[mk] if green.get((mk, t, kind))}}
            print(f"{vm:5s} {mk:26s} green{kind}: F={F:.2f} Mango 초록 {mg:.0f}ms → OR 척도 {mg / F:.0f}ms")
        json.dump(v, open(os.path.join(a.dir, f"variant_{vm}_green{kind}.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump(report, open(os.path.join(a.dir, "green_factor_20261125.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
