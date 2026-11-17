"""본 측정 공통 모듈: 설정, 키, 경로(arm), 스트리밍 호출과 기록.

측정 규칙은 docs/02_실험_프로토콜.md(v0.9.4) 3·4·6절을 따른다.
- 시계: time.perf_counter_ns(), t0 = create() 호출 직전
- 요청 전체 마감: asyncio.timeout(request_deadline_s) (httpx 단계별 타임아웃만으로는 보장 안 됨)
- 재시도 없음(max_retries=0). 연결은 호스트별 클라이언트 하나로 재사용(keep-alive)
- 연결 단계 시간: httpx trace 확장으로 TCP·TLS 이벤트를 요청별로 기록
키 값은 출력·기록하지 않는다.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import secrets
import time
import uuid
from collections import deque
from pathlib import Path

import httpx
import yaml
from openai import AsyncOpenAI

from analysis_metrics import decode_rate

HERE = Path(__file__).parent

# ---------------------------------------------------------------- 설정·키

def load_config(path: str | Path) -> dict:
    path = Path(path)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "base" in cfg:
        base = load_config(path.parent / cfg.pop("base"))
        for k, v in cfg.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = {**base[k], **v}
            else:
                base[k] = v
        cfg = base
    return cfg


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load_keys() -> dict[str, str]:
    keys = {k: os.environ.get(k, "") for k in ("MANGO_API_KEY", "OPENROUTER_API_KEY", "OPENROUTER_API_KEY_NOBC")}
    cands = [Path(os.environ["MANGO_BENCH_ENV"])] if os.environ.get("MANGO_BENCH_ENV") else []
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    cands += [Path("/opt/mango-bench/.env"), home / ".mango_bench.env", home / "mango_bench.env"]
    for f in cands:
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k in keys and not keys[k]:
                    keys[k] = v
    return keys


def slot_seed(cfg: dict, slot: str) -> int:
    return int(hashlib.sha256(f"{cfg['seed_prefix']}|slot{slot}".encode()).hexdigest()[:16], 16)


# ---------------------------------------------------------------- 경로(arm)

def arms_for(cfg: dict, model_key: str) -> dict[str, dict]:
    m = cfg["models"][model_key]
    slug = m["or_slug"]
    arms = {"M": {"arm": "M", "host": "mango", "model": m["mango_id"], "extra": {}}}
    arms["OR-default"] = {"arm": "OR-default", "host": "or", "model": slug, "extra": {}}
    for p in m["pins"]:
        arms[f"OR-pin:{p}"] = {"arm": f"OR-pin:{p}", "host": "or", "model": slug,
                               "extra": {"provider": {"order": [p], "allow_fallbacks": False}}}
    for s in cfg["sort_routes"]:
        arms[f"OR-sort:{s}"] = {"arm": f"OR-sort:{s}", "host": "or", "model": slug,
                                "extra": {"provider": {"sort": s}}}
    if (cfg.get("counts") or {}).get("w2_nosid_sessions"):
        # 탐색 조건: OR-기본과 같되 W2에 session_id를 보내지 않음 (OpenRouter 고착 규칙 비교용, v1.0)
        arms["OR-default-nosid"] = {"arm": "OR-default-nosid", "host": "or", "model": slug, "extra": {}}
    arms["OR-floor"] = {"arm": "OR-floor", "host": "or", "model": slug + ":floor", "extra": {}}
    arms["OR-nitro"] = {"arm": "OR-nitro", "host": "or", "model": slug + ":nitro", "extra": {}}
    return arms


# ---------------------------------------------------------------- 속도 제한

class RateLimiter:
    """최근 60초 안의 요청 시작 수를 rpm 이하로 유지한다."""

    def __init__(self, rpm: int):
        self.rpm, self.times, self.lock = rpm, deque(), asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                while self.times and now - self.times[0] >= 60:
                    self.times.popleft()
                if len(self.times) < self.rpm:
                    self.times.append(now)
                    return
                await asyncio.sleep(60 - (now - self.times[0]) + 0.01)


class Limits:
    def __init__(self, cfg: dict, mango_concurrency: int | None = None):
        L = cfg["limits"]
        self.global_sem = asyncio.Semaphore(L["global_concurrency"])
        self.mango_sem = asyncio.Semaphore(mango_concurrency or L["mango_concurrency"])
        self.mango_rate = RateLimiter(L["mango_rpm"])
        self.or_rate = {k: RateLimiter(L["openrouter_rpm_per_model"]) for k in cfg["models"]}

    def ctx(self, host: str, model_key: str):
        return _Slot(self, host, model_key)


class _Slot:
    def __init__(self, lim: Limits, host: str, model_key: str):
        self.lim, self.host, self.model_key = lim, host, model_key

    async def __aenter__(self):
        await self.lim.global_sem.acquire()
        if self.host == "mango":
            await self.lim.mango_sem.acquire()
            await self.lim.mango_rate.acquire()
        else:
            await self.lim.or_rate[self.model_key].acquire()

    async def __aexit__(self, *exc):
        if self.host == "mango":
            self.lim.mango_sem.release()
        self.lim.global_sem.release()


# ---------------------------------------------------------------- 클라이언트와 연결 단계 기록

_conn_events: contextvars.ContextVar[list | None] = contextvars.ContextVar("conn_events", default=None)


async def _trace(event_name: str, info: dict) -> None:
    ev = _conn_events.get()
    if ev is not None and event_name.startswith(("connection.", "http11.send_request_headers.started",
                                                  "http2.send_request_headers.started")):
        ev.append((event_name, time.perf_counter_ns()))


async def _add_trace(request: httpx.Request) -> None:
    request.extensions["trace"] = _trace


def make_clients(cfg: dict, keys: dict, mango_base: str | None = None, or_base: str | None = None) -> dict:
    def http():
        return httpx.AsyncClient(timeout=httpx.Timeout(cfg["request_deadline_s"]),
                                 limits=httpx.Limits(max_connections=64, max_keepalive_connections=64),
                                 event_hooks={"request": [_add_trace]})
    return {
        "mango": AsyncOpenAI(base_url=mango_base or cfg["mango_base"], api_key=keys.get("MANGO_API_KEY") or "x",
                             max_retries=0, http_client=http()),
        "or": AsyncOpenAI(base_url=or_base or cfg["openrouter_base"], api_key=keys.get("OPENROUTER_API_KEY") or "x",
                          max_retries=0, http_client=http()),
    }


def summarize_conn(events: list, t0: int) -> dict:
    d = {name: t for name, t in events}
    def span(a, b):
        return round((d[b] - d[a]) / 1e6, 2) if a in d and b in d else None
    return {"new_connection": "connection.connect_tcp.started" in d,
            "tcp_ms": span("connection.connect_tcp.started", "connection.connect_tcp.complete"),
            "tls_ms": span("connection.start_tls.started", "connection.start_tls.complete")}


# ---------------------------------------------------------------- 메시지

def nonce_hex() -> str:
    return secrets.token_hex(8)


def with_nonce(messages: list[dict], nonce: str) -> list[dict]:
    """첫 메시지를 nonce 시스템 메시지로. 이미 시스템 메시지로 시작하면(W2) 그 맨 앞에 붙인다."""
    if messages and messages[0]["role"] == "system":
        return [{"role": "system", "content": f"ID: {nonce}\n\n" + messages[0]["content"]}] + messages[1:]
    return [{"role": "system", "content": f"ID: {nonce}"}] + messages


def classify_error(e: Exception, host: str) -> tuple[str, str]:
    """(분류, 설명). 분류: endpoint(지표에 포함) / ours(제외·기록)."""
    status = getattr(e, "status_code", None)
    body = str(getattr(e, "body", "") or e)[:500]
    low = body.lower()
    if isinstance(e, TimeoutError):
        return "endpoint", "deadline"
    if status in (401, 402, 403):
        return "ours", f"http{status}"
    if status in (400, 404, 413, 422):
        return "ours", f"http{status}"
    if status == 429:
        if host == "mango":
            return "ours", "mango_rate_limit"
        if "new-account" in low or "rate limit exceeded" in low and "provider" not in low:
            return "ours", "or_account_rate_limit"
        return "endpoint", "provider_429"
    if status and status >= 500:
        return "endpoint", f"http{status}"
    if isinstance(e, (httpx.RemoteProtocolError, httpx.ReadError)) or "incomplete" in low:
        return "endpoint", "stream_interrupted"
    if status is None and isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "ours", "local_connect"
    return "endpoint" if status else "ours", type(e).__name__


# ---------------------------------------------------------------- 스트리밍 호출

def validate_tool(args_text: str, name: str | None, tools: list | None) -> bool:
    if not tools or not name:
        return False
    spec = next((t["function"] for t in tools if t["function"]["name"] == name), None)
    if spec is None:
        return False
    try:
        import jsonschema
        jsonschema.validate(json.loads(args_text or "{}"), spec["parameters"])
        return True
    except Exception:
        return False


async def stream_call(client: AsyncOpenAI, *, host: str, model: str, messages: list[dict], max_tokens: int,
                      temperature: float, top_p: float, extra: dict | None = None, tools: list | None = None,
                      need_tool: bool = False, deadline_s: float = 300, meta: dict | None = None,
                      on_chunk=None, keep_text: int = 400, expect_length: bool = False,
                      session_id: str | None = None) -> dict:
    """한 요청을 스트리밍으로 보내고 프로토콜 기록 스키마의 한 행을 돌려준다."""
    rid = (meta or {}).get("request_id") or str(uuid.uuid4())
    body_extra = dict(extra or {})
    if host == "or":
        body_extra.setdefault("reasoning", {"enabled": True})
        body_extra["trace"] = {"trace_id": rid}
        if session_id:  # OpenRouter 제공사 고착 키 (v1.0). Mango에는 보내지 않음
            body_extra["session_id"] = session_id[:256]
    kwargs = dict(model=model, messages=messages, stream=True, stream_options={"include_usage": True},
                  temperature=temperature, top_p=top_p, max_tokens=max_tokens)
    if tools:
        kwargs.update(tools=tools, tool_choice="auto")
    rec = {"request_id": rid, **(meta or {}), "host": host, "model": model,
           "provider_req": (extra or {}).get("provider"), "ok": False, "fail_side": None, "error": None,
           "t_start_utc": time.time()}
    events: list = []
    tok = _conn_events.set(events)
    chunks: list = []           # (ms since t0, kind) kind: r=reasoning c=content t=tool u=usage/other
    first_any = first_content = stream_end = None
    text_parts, calls = [], {}   # calls: 조각의 tool_calls[].index별로 이름·인자를 따로 모음 (병렬 호출 대응)
    reasoning_chars = 0
    usage = finish = resp_id = fingerprint = None
    headers = None
    t0 = time.perf_counter_ns()
    try:
        async with asyncio.timeout(deadline_s):
            raw = await client.chat.completions.with_raw_response.create(**kwargs, extra_body=body_extra or None)
            headers = dict(raw.headers)
            stream = raw.parse()
            async for ch in stream:
                now = time.perf_counter_ns()
                if on_chunk:
                    on_chunk(ch, now)
                resp_id = resp_id or getattr(ch, "id", None)
                fingerprint = fingerprint or getattr(ch, "system_fingerprint", None)
                if getattr(ch, "usage", None):
                    usage = ch.usage.model_dump()
                kind = "u"
                for c in ch.choices or []:
                    dd = c.delta.model_dump() if c.delta else {}
                    r = dd.get("reasoning") or dd.get("reasoning_content")
                    if r:
                        kind = "r"
                        reasoning_chars += len(r)
                    if dd.get("content"):
                        kind = "c"
                        text_parts.append(dd["content"])
                    if dd.get("tool_calls"):
                        kind = "t"
                        for j, tc in enumerate(dd["tool_calls"]):
                            tc = tc or {}
                            fn = tc.get("function") or {}
                            c_ = calls.setdefault(tc.get("index", j), {"name": None, "args": []})
                            c_["name"] = c_["name"] or fn.get("name")
                            if fn.get("arguments"):
                                c_["args"].append(fn["arguments"])
                    if c.finish_reason:
                        finish = c.finish_reason
                if kind in "rct":
                    first_any = first_any or now
                    if kind in "ct":
                        first_content = first_content or now
                chunks.append((round((now - t0) / 1e6, 2), kind))
            stream_end = time.perf_counter_ns()
        call_list = [{"name": c_["name"], "arguments": "".join(c_["args"])} for _, c_ in sorted(calls.items())]
        tool_ok = (bool(call_list) and all(validate_tool(c_["arguments"], c_["name"], tools) for c_ in call_list)
                   if need_tool else None)
        if finish == "length" and expect_length:
            # L·Z·준비 호출처럼 출력 상한을 일부러 작게 둔 요청의 정상 종료 (v1.0: 실패에서 제외)
            rec.update(ok=True, status_note="length_expected")
        elif finish == "length":
            rec.update(fail_side="endpoint", error="truncated")
        elif finish is None:
            rec.update(fail_side="endpoint", error="no_finish")
        elif need_tool and not tool_ok:
            rec.update(fail_side="endpoint", error="tool_invalid")
        else:
            rec["ok"] = True
        rec["tool_valid"] = tool_ok
    except BaseException as e:  # noqa: BLE001  실패도 결과로 기록
        if isinstance(e, asyncio.CancelledError):
            raise
        side, why = classify_error(e, host)
        rec.update(fail_side=side, error=why, status=getattr(e, "status_code", None),
                   error_detail=str(getattr(e, "body", "") or e)[:300])
    finally:
        _conn_events.reset(tok)
    ms = lambda t: None if t is None else round((t - t0) / 1e6, 2)
    u = usage or {}
    details = u.get("completion_tokens_details") or {}
    rtoks = details.get("reasoning_tokens") if details.get("reasoning_tokens") is not None else u.get("reasoning_tokens")
    cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens"))
    gen_times = [t / 1000 for t, k in chunks if k in "rct"]
    rec.update(
        resp_id=resp_id, system_fingerprint=fingerprint, finish_reason=finish,
        ttft_any_ms=ms(first_any), ttft_content_ms=ms(first_content), e2e_ms=ms(stream_end),
        prompt_tokens=u.get("prompt_tokens"), cached_tokens=cached, completion_tokens=u.get("completion_tokens"),
        reasoning_tokens=rtoks, reasoning_chars=reasoning_chars, usage=usage, chunks=chunks,
        n_chunks=len(chunks), conn=summarize_conn(events, t0), headers=headers,
        text=("".join(text_parts))[:keep_text],
        tool_calls=[{"name": c_["name"], "arguments": "".join(c_["args"])[:2000]} for _, c_ in sorted(calls.items())],
        tool_name=calls[min(calls)]["name"] if calls else None,
        tool_args="".join(calls[min(calls)]["args"])[:2000] if calls else "",
        reasoning_missing=bool(rtoks == 0),
    )
    full = rec["ok"] and rec.get("status_note") != "length_expected"  # 의도된 짧은 출력은 처리량 계산 제외
    rec["decode_tps"] = (decode_rate(u.get("completion_tokens"), rtoks, reasoning_chars > 0, gen_times)
                         if full else None)
    rec["total_tps"] = (u["completion_tokens"] / ((stream_end - t0) / 1e9)
                        if full and stream_end and u.get("completion_tokens") is not None else None)
    return rec
