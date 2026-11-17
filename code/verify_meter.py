"""측정기 검증 (파일럿 합격 기준, 프로토콜 11절): 로컬 모의 스트림 서버로 두 가지를 확인한다.

1. 측정 시각 오차: 서버가 조각을 보낸 시각과 측정 코드가 받은 시각의 차이. 동시 1·16 조건에서 중앙값 < 5ms.
2. 요청 전체 마감: 끝나지 않는 스트림이 마감 시각에 끊기고 'deadline' 실패로 기록되는지.
   기본은 빠른 확인용 5초 마감, --full이면 실제 값 300초.
실제 서비스는 호출하지 않는다. 결과는 --out 파일(JSON)로 저장.

사용: python verify_meter.py --out results/meter_check.json [--full]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics as st
import subprocess
import sys
import time
from pathlib import Path

from bench_common import make_clients, stream_call

PORT = 8099
BASE = f"http://127.0.0.1:{PORT}/v1"
CFG = {"request_deadline_s": 300, "mango_base": BASE, "openrouter_base": BASE}


async def lag_test(client, concurrency: int, requests: int, model: str) -> dict:
    lags: list[float] = []
    sem = asyncio.Semaphore(concurrency)

    def on_chunk(ch, now):
        fp = getattr(ch, "system_fingerprint", "") or ""
        if fp.startswith("ts:"):
            lags.append((now - int(fp[3:])) / 1e6)

    async def one():
        async with sem:
            return await stream_call(client, host="mango", model=model, messages=[{"role": "user", "content": "hi"}],
                                     max_tokens=100, temperature=1.0, top_p=1.0, deadline_s=60, on_chunk=on_chunk)

    recs = await asyncio.gather(*(one() for _ in range(requests)))
    lags.sort()
    return {"concurrency": concurrency, "requests": requests, "ok": sum(r["ok"] for r in recs),
            "chunks": len(lags), "median_ms": round(st.median(lags), 3),
            "p90_ms": round(lags[int(0.9 * (len(lags) - 1))], 3), "p99_ms": round(lags[int(0.99 * (len(lags) - 1))], 3),
            "max_ms": round(lags[-1], 3), "pass": st.median(lags) < 5.0 and all(r["ok"] for r in recs)}


async def deadline_test(client, deadline: float) -> dict:
    t = time.perf_counter()
    rec = await stream_call(client, host="mango", model="mock-slow", messages=[{"role": "user", "content": "hi"}],
                            max_tokens=100, temperature=1.0, top_p=1.0, deadline_s=deadline)
    el = time.perf_counter() - t
    return {"deadline_s": deadline, "elapsed_s": round(el, 3), "error": rec["error"], "fail_side": rec["fail_side"],
            "chunks_before_cut": rec["n_chunks"],
            "pass": rec["error"] == "deadline" and deadline <= el < deadline + 1.0}


async def main(out: Path, full: bool) -> None:
    srv = subprocess.Popen([sys.executable, str(Path(__file__).parent / "mock_server.py"), "--port", str(PORT)],
                           stdout=subprocess.PIPE, text=True)
    try:
        srv.stdout.readline()
        client = make_clients(CFG, {})["mango"]
        model = "mock-fast-50-20"
        await lag_test(client, 1, 2, model)  # 준비 (연결 수립)
        res = {"python": sys.version.split()[0], "platform": platform.platform(),
               "lag_c1": await lag_test(client, 1, 20, model),
               "lag_c16": await lag_test(client, 16, 64, model),
               "deadline": await deadline_test(client, 300 if full else 5)}
        res["pass"] = all(res[k]["pass"] for k in ("lag_c1", "lag_c16", "deadline"))
    finally:
        srv.terminate()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="meter_check.json")
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(Path(a.out), a.full))
