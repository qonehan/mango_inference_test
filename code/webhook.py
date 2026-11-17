"""OpenRouter Broadcast 수신기 (Webhook 목적지). 받은 본문을 날짜별 JSONL에 그대로 추가한다.

OpenRouter는 OTLP JSON(resourceSpans)을 POST/PUT으로 보낸다. 연결 시험은 빈 본문 + X-Test-Connection: true.
Broadcast 설정의 사용자 정의 헤더에 X-Bench-Token을 넣고 같은 값을 BROADCAST_TOKEN 환경변수로 주면 그 값만 받는다.
TLS는 앞단 Caddy가 처리하고, 이 프로그램은 127.0.0.1에서만 듣는다.
사용: BROADCAST_TOKEN=... python webhook.py --port 8787 --out /opt/mango-bench/broadcast
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOCK = threading.Lock()


def make_handler(out: Path, token: str | None):
    class H(BaseHTTPRequestHandler):
        def _recv(self):
            if token and self.headers.get("X-Bench-Token") != token:
                self.send_response(401)
                self.end_headers()
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            rec = {"received_utc": time.time(), "test": self.headers.get("X-Test-Connection") == "true",
                   "body": body.decode("utf-8", errors="replace")}
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            with LOCK, open(out / f"{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.send_response(200)
            self.end_headers()

        do_POST = do_PUT = _recv

        def log_message(self, *a):
            pass

    return H


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--out", default="broadcast")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(out, os.environ.get("BROADCAST_TOKEN"))).serve_forever()
