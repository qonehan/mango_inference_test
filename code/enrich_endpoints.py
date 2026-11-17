"""OpenRouter 응답의 실제 엔드포인트(제공사/변형/지역)를 기록에 채운다.

generation 조회의 provider_responses[].endpoint_id(UUID)를 OpenRouter 모델 페이지가 쓰는 공개 통계
(/api/frontend/v1/stats/endpoint?permaslug=…)의 엔드포인트 목록과 맞춰 provider_slug(예: wafer/us),
양자화, 서비스 등급, 지역, 단가로 바꾼다. 새 모델 호출은 하지 않는다.

사용: python enrich_endpoints.py <raw.jsonl이 있는 폴더> [하위 재시험 폴더도 자동 처리]
출력: 각 폴더의 endpoints.jsonl (request_id별 시도 목록과 엔드포인트 정보)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from smoke_test import load_keys  # noqa: E402

OR = "https://openrouter.ai/api/v1"
STATS = "https://openrouter.ai/api/frontend/v1/stats/endpoint"


def endpoint_table(http: httpx.Client, permaslug: str, cache: dict) -> dict:
    if permaslug not in cache:
        r = http.get(STATS, params={"permaslug": permaslug}, headers={"User-Agent": "mango-bench"})
        r.raise_for_status()
        cache[permaslug] = {e["id"]: {
            "endpoint": e.get("provider_slug"), "provider": e.get("provider_name"),
            "quantization": e.get("quantization"), "region": e.get("provider_region"),
            "variant": e.get("variant"), "service_tier": e.get("derived_service_tier"),
            "price_in": (e.get("pricing") or {}).get("prompt"), "price_out": (e.get("pricing") or {}).get("completion"),
            "price_cache_read": (e.get("pricing") or {}).get("input_cache_read"),
        } for e in r.json().get("data", [])}
    return cache[permaslug]


def enrich(folder: Path, http: httpx.Client, key: str, cache: dict) -> int:
    out = []
    for line in (folder / "raw.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["host"] != "or" or not rec.get("resp_id"):
            continue
        g = http.get(f"{OR}/generation", params={"id": rec["resp_id"]}, headers={"Authorization": f"Bearer {key}"})
        if g.status_code != 200:
            out.append({"request_id": rec["request_id"], "error": g.status_code})
            continue
        d = g.json()["data"]
        attempts = []
        for p in d.get("provider_responses") or []:
            info = endpoint_table(http, p.get("model_permaslug"), cache).get(p.get("endpoint_id"), {})
            attempts.append({"endpoint_id": p.get("endpoint_id"), "status": p.get("status"),
                             "latency_ms": p.get("latency"), **info})
        out.append({"request_id": rec["request_id"], "model_key": rec["model_key"], "arm": rec["arm"],
                    "kind": rec["kind"], "permaslug": d.get("model"), "service_tier": d.get("service_tier"),
                    "attempts": attempts, "final_endpoint": attempts[-1].get("endpoint") if attempts else None})
        time.sleep(0.2)
    with open(folder / "endpoints.jsonl", "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    return len(out)


if __name__ == "__main__":
    root = Path(sys.argv[1])
    key = load_keys()["OPENROUTER_API_KEY"]
    cache: dict = {}
    with httpx.Client(timeout=30) as http:
        for folder in [root] + sorted(p for p in root.iterdir() if p.is_dir() and (p / "raw.jsonl").exists()):
            print(folder.name, enrich(folder, http, key, cache))
