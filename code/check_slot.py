"""회차 점검과 봉인 (프로토콜 11절, 부록 A-5).

- 경로 × 모델별 건수·성공·엔드포인트 쪽 실패·우리 쪽 실패·미측정, 실패율 50% 초과 경로 표시
- 성공 요청의 필수 필드 결측
- 결합률: OpenRouter 요청의 generation 조회, Broadcast(trace_id) 수신 (기준 99%)
- 지출: OpenRouter total_cost 합, Mango는 토큰 × GET /v1/models 단가(추정, 실제 청구와 분리)
- 파일 SHA-256을 manifest.json의 "seal"에 기록
사용: python check_slot.py runs/2026-11-18/S1 [--broadcast /opt/mango-bench/broadcast]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from bench_common import file_sha256

REQUIRED = ("ttft_any_ms", "e2e_ms", "prompt_tokens", "completion_tokens")


def broadcast_trace_ids(folder: Path | None) -> set[str]:
    """Broadcast 원본에서 결합 키(응답 ID gen-..., 요청의 trace_id)를 모은다."""
    ids: set[str] = set()
    if not folder or not folder.exists():
        return ids
    for f in folder.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                body = json.loads(json.loads(line)["body"] or "{}")
            except (json.JSONDecodeError, KeyError):
                continue
            for rs in body.get("resourceSpans", []):
                for a in rs.get("resource", {}).get("attributes", []):
                    if a.get("key") == "openrouter.trace.id":
                        ids.update(str(v) for v in a.get("value", {}).values())
                for ss in rs.get("scopeSpans", []):
                    for sp in ss.get("spans", []):
                        # OpenRouter는 traceId를 자체 해시로 바꾸므로 응답 ID(gen-...)로 결합한다
                        for a in sp.get("attributes", []):
                            if a.get("key") == "gen_ai.response.id":
                                v = str(next(iter(a.get("value", {}).values()), ""))
                                ids.add(v.split(":")[0])
    return ids


def mango_cost(rec: dict, prices: dict) -> float | None:
    p = prices.get(rec.get("model"))
    if not p or rec.get("prompt_tokens") is None:
        return None
    cached = rec.get("cached_tokens") or 0
    read = (p.get("cache") or {}).get("read", p["input"])
    return ((rec["prompt_tokens"] - cached) * p["input"] + cached * read
            + (rec.get("completion_tokens") or 0) * p["output"]) / 1e6


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--broadcast", default="")
    a = ap.parse_args()
    d = Path(a.folder)
    recs = [json.loads(l) for l in (d / "raw.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    recs = [r for r in recs if r.get("task") != "WARMUP"]
    gens = {}
    if (d / "generations.jsonl").exists():
        for l in (d / "generations.jsonl").read_text(encoding="utf-8").splitlines():
            g = json.loads(l)
            if "generation_error" not in g:
                gens[g["request_id"]] = g
    prices = {}
    if (d / "mango_models.json").exists():
        prices = {m["id"]: m.get("cost") for m in json.loads((d / "mango_models.json").read_text(encoding="utf-8"))["data"]}
    bids = broadcast_trace_ids(Path(a.broadcast)) if a.broadcast else set()

    cells = defaultdict(lambda: defaultdict(int))
    missing, or_total, or_gen, or_bc = 0, 0, 0, 0
    spend = {"openrouter": 0.0, "mango_estimate": 0.0, "mango_unpriced": 0}
    for r in recs:
        c = cells[(r["model_key"], r["arm"])]
        c["n"] += 1
        if r.get("status_note") in ("unmeasured", "session_stopped"):
            c[r["status_note"]] += 1
            continue
        c["ok" if r.get("ok") else f"fail_{r.get('fail_side')}"] += 1
        if r.get("ok") and r.get("task") not in ("Z",) and any(r.get(k) is None for k in REQUIRED):
            missing += 1
        if r.get("host") == "or":
            or_total += 1
            if r["request_id"] in gens:
                or_gen += 1
                spend["openrouter"] += gens[r["request_id"]].get("total_cost") or 0
            if r.get("resp_id") in bids or r["request_id"] in bids:
                or_bc += 1
        elif r.get("host") == "mango":
            mc = mango_cost(r, prices)
            if mc is None:
                spend["mango_unpriced"] += 1
            else:
                spend["mango_estimate"] += mc
    table, flagged = [], []
    for (mk, arm), c in sorted(cells.items()):
        sent = c["n"] - c["unmeasured"] - c["session_stopped"]
        fr = c["fail_endpoint"] / sent if sent else None
        table.append({"model": mk, "arm": arm, **c, "endpoint_fail_rate": None if fr is None else round(fr, 3)})
        if fr is not None and fr > 0.5:
            flagged.append(f"{mk} {arm} {fr:.0%}")
    gen_rate = or_gen / or_total if or_total else None
    bc_rate = or_bc / or_total if or_total and a.broadcast else None
    report = {"requests": len(recs), "missing_required_fields": missing,
              "generation_join_rate": gen_rate, "broadcast_join_rate": bc_rate,
              "spend_usd": {k: round(v, 4) if isinstance(v, float) else v for k, v in spend.items()},
              "over_50pct_endpoint_failures": flagged,
              "alerts": [x for x in [
                  "필수 필드 결측" if missing else None,
                  "generation 결합률 99% 미만" if gen_rate is not None and gen_rate < 0.99 else None,
                  "Broadcast 결합률 99% 미만" if bc_rate is not None and bc_rate < 0.99 else None,
                  "실패율 50% 초과 경로: 다음 회차부터 탐색 경로만 확인 호출로 축소" if flagged else None] if x],
              "cells": table}
    (d / "check.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    mf = d / "manifest.json"
    old = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    old["seal"] = {p.name: file_sha256(p) for p in sorted(d.iterdir()) if p.is_file() and p.name != "manifest.json"}
    old["check"] = {k: v for k, v in report.items() if k != "cells"}
    mf.write_text(json.dumps(old, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(old["check"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
