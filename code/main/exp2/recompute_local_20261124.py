#!/usr/bin/env python3
"""2-2회 판정을 로컬 원본으로 다시 계산하고, TTFT 환산 민감도용 요약을 만든다 (2026-11-24).

결과 문서: 02_분석/32_실험2-1회_최종_2-2회_결과정리_20261123.md 6~7절

1) refs  : 로컬 공개 원본(seoul/public/public)으로 analyze_interim_20261122.py의 public 단계를 그대로 실행
2) summary: 로컬 records로 summary 단계를 그대로 실행(VM과 같은 코드·같은 난수 시드)
3) colo  : latency-comparison(colo=ICN/IAD) 1시간 칸에서 회차 시각 칸(xx:00, xx+1:00)의 경로별 공개 TTFT
4) variants: 반사실 계산용 요약 사본. Mango의 OR 척도 TTFT만 바꾼다.
   - base      : 본문 TTFT ÷ 전체 공개 p50 환산 계수(VM 집계와 같은 방식)
   - first     : 첫 조각 TTFT ÷ 같은 공개 p50 환산 계수(Mango도 첫 조각)
   - colo      : 본문 TTFT ÷ VM 위치 colo 공개값(서울 ICN, 미국 동부 IAD) 환산 계수
   - first_colo: 첫 조각 + colo
   - gw{d}     : base에 OR 경유 지연 d ms를 우리 측정 척도에서 더함 → (Mango 본문 TTFT + d) ÷ 환산 계수

runner_20261122.py는 VM 전용 모듈을 불러오므로 PATHS만 소스에서 읽어 넣는다.
사용: python recompute_local_20261124.py --vmdir <vm_결과_20261123> --out <출력 폴더>
"""
import argparse
import ast
import contextlib
import copy
import glob
import gzip
import io
import json
import math
import os
import re
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

COLO_CELLS = {"r1": ("2026-11-22 05:00:00", "2026-11-22 06:00:00"), "r2": ("2026-11-22 11:00:00", "2026-11-22 12:00:00"),
              "r3": ("2026-11-22 17:00:00", "2026-11-22 18:00:00"), "r4": ("2026-11-22 23:00:00", "2026-11-23 00:00:00")}
VM_COLO = {"seoul": "ICN", "iad": "IAD"}
GW_MS = (30, 60, 100, 150)


def colo_refs(pub):
    """(colo, mk, tag) -> round -> 공개 TTFT(칸 값 평균). 칸마다 가장 늦게 수집한 확정값."""
    id2tag = defaultdict(dict)
    cells = {}
    rows = []
    for p in sorted(glob.glob(pub + "/*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    for r in rows:
        if r.get("api") == "endpoint" and r.get("status") == 200:
            try:
                for x in json.loads(r["body"])["data"]:
                    id2tag[r["model_key"]][x["id"]] = x.get("provider_slug")
            except (ValueError, KeyError, TypeError):
                pass
    for r in rows:
        if r.get("api") != "latency-comparison" or r.get("status") != 200:
            continue
        m = re.search(r"colo=([A-Z]+)", r.get("url") or "")
        if not m:
            continue
        try:
            body = json.loads(r["body"])["data"]
        except (ValueError, KeyError, TypeError):
            continue
        for cell in body[:-1]:                       # 마지막 칸은 진행 중
            for eid, v in cell["y"].items():
                k = (m[1], r["model_key"], cell["x"], eid)
                if v is not None and (k not in cells or cells[k][0] < r["request_ts"]):
                    cells[k] = (r["request_ts"], float(v))
    out = defaultdict(dict)
    for (colo, mk, x, eid), (_, v) in cells.items():
        tag = id2tag[mk].get(eid)
        for rid, xs in COLO_CELLS.items():
            if x in xs and tag:
                out[f"{colo}|{mk}|{tag}"].setdefault(rid, []).append(v)
    return {k: {rid: statistics.mean(v) for rid, v in d.items()} for k, d in out.items()}


def wgeo(vals, w):
    num = sum(w[t] * math.log(v) for t, v in vals.items() if v and t in w)
    den = sum(w[t] for t, v in vals.items() if v and t in w)
    return math.exp(num / den) if den else None


def factor(summary_m, mk, key, pub_by_tag):
    """경로별 (우리 중앙값[key] ÷ 회차 공개값 평균) → 배정 가중 기하평균."""
    fac = {}
    for t in AI.PATHS[mk]:
        ours = summary_m["paths"][t].get(key)
        pub = [v for v in (pub_by_tag.get(t) or {}).values() if v]
        if ours and pub:
            fac[t] = ours / statistics.mean(pub)
    return wgeo(fac, AI.base_w(mk)), fac


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vmdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    pub = os.path.join(a.vmdir, "seoul", "public", "public")
    refs_p = os.path.join(a.out, "refs_public_local_20261124.json")
    AI.cmd_public(argparse.Namespace(pub=pub, out=refs_p))
    refs = json.load(open(refs_p, encoding="utf-8"))
    colo = colo_refs(pub)
    json.dump(colo, open(os.path.join(a.out, "refs_colo_local_20261124.json"), "w", encoding="utf-8"), ensure_ascii=False)
    sens = {}
    for vm in ("seoul", "iad"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            AI.cmd_summary(argparse.Namespace(run=os.path.join(a.vmdir, vm, "run"), refs=refs_p, boot=a.boot))
        s = json.loads(buf.getvalue())
        json.dump(s, open(os.path.join(a.out, f"summary_{vm}_local_20261124.json"), "w", encoding="utf-8"), ensure_ascii=False)
        variants = {}
        for mk, m in s["models"].items():
            glob_pub = {t: {rid: d["ttft"] for rid, d in refs["ep"].get(f"{mk}|{t}", {}).items()} for t in AI.PATHS[mk]}
            colo_pub = {t: colo.get(f"{VM_COLO[vm]}|{mk}|{t}", {}) for t in AI.PATHS[mk]}
            mt = m["paths"]["mango"]
            F_base, _ = factor(m, mk, "S_ttft", glob_pub)
            F_first, ff = factor(m, mk, "S_first", glob_pub)
            F_colo, fc = factor(m, mk, "S_ttft", colo_pub)
            F_fc, _ = factor(m, mk, "S_first", colo_pub)
            vals = {"base": (F_base, mt["S_ttft"] / F_base),
                    "first": (F_first, mt["S_first"] / F_first),
                    "colo": (F_colo, mt["S_ttft"] / F_colo if F_colo else None),
                    "first_colo": (F_fc, mt["S_first"] / F_fc if F_fc else None)}
            for d in GW_MS:
                vals[f"gw{d}"] = (F_base, (mt["S_ttft"] + d) / F_base)
            sens.setdefault(vm, {})[mk] = {k: {"factor": f, "mango_ttft_or": v} for k, (f, v) in vals.items()}
            sens[vm][mk]["mango_ms"] = {"S_ttft": mt["S_ttft"], "S_first": mt["S_first"]}
            sens[vm][mk]["colo_factor_paths"] = fc
            for k, (_, v) in vals.items():
                sv = variants.setdefault(k, copy.deepcopy(s))
                sv["models"][mk]["calib"]["mango_ttft_or_scale"] = v
        for k, sv in variants.items():
            json.dump(sv, open(os.path.join(a.out, f"variant_{vm}_{k}.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(sens, open(os.path.join(a.out, "sensitivity_ttft_20261124.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for vm, d in sens.items():
        for mk, x in d.items():
            print(vm, mk, " | ".join(f"{k} F={v['factor']:.2f} T={v['mango_ttft_or']:.0f}" for k, v in x.items()
                                     if isinstance(v, dict) and "factor" in v and v["factor"] and v["mango_ttft_or"]))


if __name__ == "__main__":
    main()
