"""측정기 검증용 로컬 모의 스트림 서버 (OpenAI 호환 /v1/chat/completions, SSE).

각 조각의 system_fingerprint에 서버 송신 시각 "ts:<perf_counter_ns>"를 넣는다. 같은 컴퓨터에서
perf_counter는 프로세스 사이에 같은 단조 시계(Linux CLOCK_MONOTONIC, Windows QPC)라 클라이언트 도착
시각과 직접 비교할 수 있다.

모델 이름으로 동작을 정한다:
  mock-fast-<조각수>-<간격ms>   예: mock-fast-50-20  (첫 조각 전 대기 = 간격)
  mock-slow                     1초마다 한 조각을 끝없이 보냄 (요청 전체 마감 시험용)
사용: python mock_server.py --port 8099
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time


def chunk(model: str, delta: dict, finish=None, usage=None) -> bytes:
    body = {"id": "mock-1", "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
            "system_fingerprint": f"ts:{time.perf_counter_ns()}",
            "choices": [] if usage else [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    data = f"data: {json.dumps(body)}\n\n".encode()
    return f"{len(data):x}\r\n".encode() + data + b"\r\n"


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            hdr = {l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
            body = await reader.readexactly(int(hdr.get("content-length", 0)))
            req = json.loads(body or b"{}")
            model = req.get("model", "mock-fast-20-20")
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n"
                         b"connection: keep-alive\r\n\r\n")
            await writer.drain()
            if model.startswith("mock-slow"):
                while True:
                    await asyncio.sleep(1.0)
                    writer.write(chunk(model, {"content": "x"}))
                    await writer.drain()
            if model.startswith("mock-fast-"):
                _, _, n, gap = model.split("-")
                n, gap = int(n), int(gap) / 1000
            else:  # 실제 모델 ID로 runner 전체 흐름을 시험할 때
                n, gap = 20, 0.005
            use_tool = bool(req.get("tools"))
            start = time.perf_counter()
            for i in range(n):
                # 누적 오차가 쌓이지 않게 목표 시각에 맞춰 보냄
                await asyncio.sleep(max(0.0, start + gap * (i + 1) - time.perf_counter()))
                last = i == n - 1
                if use_tool and i == n - 2:  # 병렬 도구 호출: index가 다른 두 호출을 차례로 보냄
                    tc = {"index": 0, "id": "call_mock0", "type": "function",
                          "function": {"name": "read_file", "arguments": json.dumps({"path": "Lib/x.py"})}}
                    writer.write(chunk(model, {"tool_calls": [tc]}))
                elif use_tool and last:
                    tc = {"index": 1, "id": "call_mock1", "type": "function",
                          "function": {"name": "search_code", "arguments": json.dumps({"query": "def main"})}}
                    writer.write(chunk(model, {"tool_calls": [tc]}, "tool_calls"))
                elif i < 3:
                    writer.write(chunk(model, {"reasoning": f"r{i} "}))
                else:
                    writer.write(chunk(model, {"content": f"t{i} "}, "stop" if last else None))
                await writer.drain()
            writer.write(chunk(model, {}, usage={"prompt_tokens": 10, "completion_tokens": n, "total_tokens": 10 + n,
                                                 "completion_tokens_details": {"reasoning_tokens": 0}}))
            data = b"data: [DONE]\n\n"
            writer.write(f"{len(data):x}\r\n".encode() + data + b"\r\n0\r\n\r\n")
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        writer.close()


async def main(port: int) -> None:
    server = await asyncio.start_server(handle, "127.0.0.1", port)
    print(f"mock server on 127.0.0.1:{port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    asyncio.run(main(ap.parse_args().port))
