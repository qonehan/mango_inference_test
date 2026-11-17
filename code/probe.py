"""회차 시작 기록과 연결 측정, 회차 중 대조군 측정 (프로토콜 4·8·9절, 부록 A-4).

start:   환경(OS·Python·패키지·코드 커밋), 시각 동기화 오차(chronyc), 호스트 IP·ASN·traceroute,
         호스트마다 새 TCP+TLS 연결 10회, Mango GET /v1/models와 OpenRouter 엔드포인트 정보 스냅샷
         → probes.jsonl, mango_models.json, or_endpoints.json, manifest.json의 "start"
control: 60초마다 중립 기준 호스트 2곳과 두 API 호스트의 TCP 연결 시간 → control.jsonl (runner가 자동 실행)
사용: python probe.py start --config config/day1.yaml --out runs/2026-11-18/S1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

import httpx

from bench_common import load_config, load_keys

API_HOSTS = {"mango": "api.mangoboost.io", "openrouter": "openrouter.ai"}
# 중립 기준: 클라우드 내부(AWS 메타데이터 주소, 다른 클라우드면 바꿔서 기록), 제3자 CDN
NEUTRAL = {"cloud_internal": ("169.254.169.254", 80), "third_party_cdn": ("www.cloudflare.com", 443)}


def run(cmd: list[str], timeout=60) -> str:
    if not shutil.which(cmd[0]):
        return f"{cmd[0]}: not installed"
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


async def tcp_time(host: str, port: int, timeout=5.0) -> float | None:
    t = time.perf_counter_ns()
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        dt = (time.perf_counter_ns() - t) / 1e6
        w.close()
        return round(dt, 2)
    except Exception:  # noqa: BLE001
        return None


async def tcp_tls_time(host: str) -> dict:
    loop = asyncio.get_running_loop()
    addr = (await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM))[0][4][0]
    t0 = time.perf_counter_ns()
    r, w = await asyncio.open_connection(addr, 443)
    t1 = time.perf_counter_ns()
    await w.start_tls(ssl.create_default_context(), server_hostname=host)
    t2 = time.perf_counter_ns()
    w.close()
    return {"ip": addr, "tcp_ms": round((t1 - t0) / 1e6, 2), "tls_ms": round((t2 - t1) / 1e6, 2)}


async def control_loop(cfg: dict, path: Path, every_s: int = 60) -> None:
    targets = {**NEUTRAL, **{k: (h, 443) for k, h in API_HOSTS.items()}}
    with open(path, "a", encoding="utf-8") as f:
        while True:
            vals = await asyncio.gather(*(tcp_time(h, p) for h, p in targets.values()))
            f.write(json.dumps({"t_utc": time.time(), **dict(zip(targets, vals))}) + "\n")
            f.flush()
            await asyncio.sleep(every_s)


async def start(cfg: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    keys = load_keys()
    pk = ("openai", "httpx", "tiktoken", "pyyaml", "jsonschema")
    env = {"t_utc": time.time(), "platform": platform.platform(), "python": sys.version.split()[0],
           "packages": {p: (metadata.version(p) if _has(p) else None) for p in pk},
           "git_commit": run(["git", "-C", str(Path(__file__).parent), "rev-parse", "HEAD"]),
           "chrony": run(["chronyc", "tracking"]), "hostname": socket.gethostname()}
    hosts = {}
    for name, h in API_HOSTS.items():
        ip = socket.gethostbyname(h)
        hosts[name] = {"host": h, "ip": ip,
                       "asn_whois": run(["whois", "-h", "whois.cymru.com", f" -v {ip}"]),
                       "traceroute": run(["traceroute", "-n", "-w", "2", "-q", "1", h], timeout=90),
                       "connect": [await tcp_tls_time(h) for _ in range(10)]}
    with open(out / "probes.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"env": env, "hosts": hosts}, ensure_ascii=False) + "\n")
    async with httpx.AsyncClient(timeout=30) as http:
        if keys["MANGO_API_KEY"]:
            r = await http.get(f"{cfg['mango_base']}/models", headers={"Authorization": f"Bearer {keys['MANGO_API_KEY']}"})
            (out / "mango_models.json").write_text(r.text, encoding="utf-8")
        eps = {}
        for mk, m in cfg["models"].items():
            r = await http.get(f"{cfg['openrouter_base']}/models/{m['or_slug']}/endpoints")
            eps[mk] = r.json() if r.status_code == 200 else {"error": r.status_code}
        (out / "or_endpoints.json").write_text(json.dumps(eps, ensure_ascii=False), encoding="utf-8")
    mf = out / "manifest.json"
    old = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    off = next((l for l in env["chrony"].splitlines() if l.startswith("System time")), None)
    mf.write_text(json.dumps({**old, "start": {"env": env, "chrony_offset": off,
                                              "host_ips": {k: v["ip"] for k, v in hosts.items()}}},
                             indent=2, ensure_ascii=False), encoding="utf-8")
    print("시작 기록 완료:", out, "| 시각 오차:", off)


def _has(p: str) -> bool:
    try:
        metadata.version(p)
        return True
    except metadata.PackageNotFoundError:
        return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["start"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    asyncio.run(start(load_config(a.config), Path(a.out)))
