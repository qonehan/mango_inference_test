"""회차가 끝난 뒤 OpenRouter 요청마다 generation 조회와 응답 엔드포인트 식별을 한다 (프로토콜 6절 ②).

마지막 요청 60초 후 실행. 404면 30초 간격 최대 5회 재조회. 결과: generations.jsonl
엔드포인트: provider_responses[].endpoint_id를 공개 엔드포인트 목록(/api/frontend/v1/stats/endpoint)과 대조.
사용: python fetch_generations.py runs/2026-11-18/S1
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

from bench_common import load_keys

OR = "https://openrouter.ai/api/v1"
STATS = "https://openrouter.ai/api/frontend/v1/stats/endpoint"
FIELDS = ("provider_name", "total_cost", "latency", "generation_time", "native_tokens_prompt",
          "native_tokens_completion", "native_tokens_reasoning", "native_tokens_cached", "service_tier", "model")


async def endpoint_table(http, permaslug, cache, lock):
    async with lock:
        if permaslug not in cache:
            r = await http.get(STATS, params={"permaslug": permaslug}, headers={"User-Agent": "mango-bench"})
            cache[permaslug] = {e["id"]: {
                "endpoint": e.get("provider_slug"), "provider": e.get("provider_name"),
                "quantization": e.get("quantization"), "region": e.get("provider_region"),
                "variant": e.get("variant"), "service_tier": e.get("derived_service_tier"),
                "price_in": (e.get("pricing") or {}).get("prompt"),
                "price_out": (e.get("pricing") or {}).get("completion"),
                "price_cache_read": (e.get("pricing") or {}).get("input_cache_read"),
            } for e in (r.json().get("data", []) if r.status_code == 200 else [])}
    return cache[permaslug]


async def fetch(http, key, rec, cache, lock, sem):
    async with sem:
        for attempt in range(6):
            r = await http.get(f"{OR}/generation", params={"id": rec["resp_id"]},
                               headers={"Authorization": f"Bearer {key}"})
            if r.status_code == 200:
                break
            await asyncio.sleep(30)
        if r.status_code != 200:
            return {"request_id": rec["request_id"], "generation_error": r.status_code}
        d = r.json().get("data", {})
        attempts = []
        for p in d.get("provider_responses") or []:
            info = (await endpoint_table(http, p.get("model_permaslug") or d.get("model"), cache, lock)
                    ).get(p.get("endpoint_id"), {})
            attempts.append({"endpoint_id": p.get("endpoint_id"), "provider_name": p.get("provider_name"),
                             "status": p.get("status"), "latency_ms": p.get("latency"), **info})
        return {"request_id": rec["request_id"], "resp_id": rec["resp_id"], **{k: d.get(k) for k in FIELDS},
                "first_endpoint": attempts[0].get("endpoint") if attempts else None,
                "final_endpoint": attempts[-1].get("endpoint") if attempts else None,
                "attempts": attempts}


async def main(folder: Path) -> None:
    key = load_keys()["OPENROUTER_API_KEY"]
    recs = [json.loads(l) for l in (folder / "raw.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    todo = [r for r in recs if r.get("host") == "or" and r.get("resp_id")]
    out = folder / "generations.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(l)["request_id"] for l in out.read_text(encoding="utf-8").splitlines()
                if l.strip() and "generation_error" not in l}
    todo = [r for r in todo if r["request_id"] not in done]
    cache, lock, sem = {}, asyncio.Lock(), asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=30) as http:
        res = await asyncio.gather(*(fetch(http, key, r, cache, lock, sem) for r in todo))
    with open(out, "a", encoding="utf-8") as f:
        for g in res:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")
    print(f"조회 {len(res)}건, 실패 {sum('generation_error' in g for g in res)}건 → {out}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
