"""OpenRouter 모델 페이지 공개 통계 요약(JSON)을 엔드포인트 단위 CSV로 펼친다.

입력: data/openrouter_monthly_frontend_stats_20261116.json (상위 5개 제공사의 엔드포인트, 2026-11-16 수집)
출력: data/openrouter_endpoint_stats_20261116.csv (엔드포인트 1개 = 1행)

16 문서의 제공사 값은 이 엔드포인트 값들을 가격 표본 토큰으로 가중평균한 것이다. 실험의 고정 경로는
엔드포인트 단위이므로, 공개 통계와 맞춰 볼 때는 이 CSV의 해당 엔드포인트 행을 쓴다.
네트워크 호출 없음. 사용: python build_endpoint_stats.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
SRC = DATA / "openrouter_monthly_frontend_stats_20261116.json"
OUT = DATA / "openrouter_endpoint_stats_20261116.csv"

# 실험 프로토콜(19 문서)의 고정 엔드포인트
FIXED = {
    "deepseek/deepseek-v4.1-flash-1017": {"deepseek", "deepinfra/fp8", "together", "inference-net/us", "wafer/us"},
    "z-ai/glm-5.3": {"wafer/us", "inference-net/us", "z-ai/fp8", "siliconflow/fp8", "decart/fp4"},
    "z-ai/glm-5.3-flash": {"together", "inference-net/us", "open-inference/fp4", "coreweave/nvfp4", "z-ai/fp8"},
    "moonshotai/kimi-k3.1-pro": {"moonshotai/mxfp4", "inference-net/mxfp4", "modal/mxfp4", "wafer/us", "together"},
}
PERF = {"thr": "처리속도_tok_s", "lat": "첫토큰_ms", "e2e": "전체지연_ms", "tool": "도구호출오류_pct",
        "so": "구조화출력오류_pct", "cache": "캐시적중_pct"}


def main() -> None:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    cols = ["model", "provider", "endpoint", "fixed_in_protocol", "quantization", "region", "service_tier",
            "price_sample_tokens", "price_sample_share_in_model", "rank_within_provider",
            "listed_in", "listed_out", "listed_cache_read", "effective_in_1m", "effective_out_1m", "cache_hit"]
    for k, name in PERF.items():
        cols += [f"{name}_median", f"{name}_min", f"{name}_max"]
    rows = []
    for model, m in d["models"].items():
        by_p: dict[str, list] = {}
        for r in m["rows"]:
            by_p.setdefault(r["p"], []).append(r)
        for p, eps in by_p.items():
            for rank, r in enumerate(sorted(eps, key=lambda x: -(x.get("tok") or 0)), 1):
                row = {"model": model, "provider": r.get("nm"), "endpoint": r.get("tag"),
                       "fixed_in_protocol": r.get("tag") in FIXED.get(model, set()),
                       "quantization": r.get("q"), "region": r.get("reg"), "service_tier": r.get("tier"),
                       "price_sample_tokens": r.get("tok"), "price_sample_share_in_model": r.get("tokSh"),
                       "rank_within_provider": rank, "listed_in": r.get("lIn"), "listed_out": r.get("lOut"),
                       "listed_cache_read": r.get("lCR"), "effective_in_1m": r.get("effIn"),
                       "effective_out_1m": r.get("effOut"), "cache_hit": r.get("ch")}
                for k, name in PERF.items():
                    v = (r.get("perf") or {}).get(k)
                    row[f"{name}_median"], row[f"{name}_min"], row[f"{name}_max"] = (v[2], v[1], v[3]) if v else (None,) * 3
                rows.append(row)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    fixed = sum(r["fixed_in_protocol"] for r in rows)
    print(f"{len(rows)}행 (고정 엔드포인트 {fixed}개) → {OUT.name}")


if __name__ == "__main__":
    main()
