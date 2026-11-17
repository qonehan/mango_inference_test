"""Mango vs OpenRouter 간이 테스트: 모든 호출 경로를 모델마다 한 번씩 호출한다.

본 실험(프로토콜 v0.9) 전에 경로·모델 ID·응답 형식·기록 필드가 제대로 동작하는지 확인하는 용도다.
측정값은 1회 호출이라 성능 비교에 쓰지 않는다.

키는 환경변수 MANGO_API_KEY, OPENROUTER_API_KEY 또는 저장소 밖 파일
%USERPROFILE%\\.mango_bench.env (KEY=VALUE 형식)에서 읽는다. 키 값은 출력·기록하지 않는다.

사용:
  python smoke_test.py --list-mango-models     # Mango 모델 ID 확인
  python smoke_test.py --dry-run               # 호출 없이 요청 목록만 출력
  python smoke_test.py --out <폴더>            # 실제 호출 (결과: raw.jsonl, summary.csv)
  python enrich_endpoints.py <폴더>            # 응답한 엔드포인트(제공사/변형/지역·양자화) 채우기
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path

import httpx
from analysis_metrics import decode_rate
from openai import AsyncOpenAI

MANGO_BASE = "https://api.mangoboost.io/v1"
REQUEST_DEADLINE_S = 300  # 요청 전체(연결~스트림 끝) 마감
OR_BASE = "https://openrouter.ai/api/v1"

# OpenRouter slug와 고정 대상 제공사(OpenRouter provider slug). Mango 모델 ID는 실행 시 /v1/models로 확인해 채운다.
MODELS = {
    "deepseek-v4.1-flash-1017": {
        "or_slug": "deepseek/deepseek-v4.1-flash-1017",
        "pins": ["deepseek", "deepinfra", "together", "inference-net", "wafer"],
        "mango_hint": ["deepseek", "1017"],
    },
    "glm-5.3": {
        "or_slug": "z-ai/glm-5.3",
        "pins": ["wafer", "inference-net", "z-ai", "siliconflow", "decart"],
        "mango_hint": ["glm-5.3"],
        "mango_exclude": ["flash"],
    },
    "glm-5.3-flash": {
        "or_slug": "z-ai/glm-5.3-flash",
        "pins": ["together", "inference-net", "open-inference", "coreweave", "z-ai"],
        "mango_hint": ["glm-5.3", "flash"],
    },
    # Mango /v1/models에 moonshotai/Kimi-K3.1-Pro가 있어 프로토콜대로 K3.1 Pro를 쓴다 (Kimi-K3도 별도로 있음).
    "kimi-k3.1-pro": {
        "or_slug": "moonshotai/kimi-k3.1-pro",
        "pins": ["moonshotai", "inference-net", "modal", "wafer", "together"],
        "mango_hint": ["kimi", "k3.1"],
    },
}

PROMPT = "Explain in two sentences what a hash table is and when to use one."
TOOL_PROMPT = "Read the file src/app.py to find the bug. Use the provided tool."
TOOLS = [{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Return the contents of a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
}]


def load_keys() -> dict[str, str]:
    keys = {k: os.environ.get(k, "") for k in ("MANGO_API_KEY", "OPENROUTER_API_KEY")}
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    f = next((p for p in (home / ".mango_bench.env", home / "mango_bench.env") if p.exists()), None)
    if f:
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k in keys and not keys[k]:
                    keys[k] = v
    return keys


def arms_for(model: dict, mango_id: str | None) -> list[dict]:
    slug = model["or_slug"]
    arms = [{"arm": "M", "host": "mango", "model": mango_id, "extra": {}}]
    arms.append({"arm": "OR-default", "host": "or", "model": slug, "extra": {}})
    for p in model["pins"]:
        arms.append({"arm": f"OR-pin-{p}", "host": "or", "model": slug,
                     "extra": {"provider": {"order": [p], "allow_fallbacks": False}}})
    for s in ("price", "throughput", "latency"):
        arms.append({"arm": f"OR-sort-{s}", "host": "or", "model": slug, "extra": {"provider": {"sort": s}}})
    arms.append({"arm": "OR-floor", "host": "or", "model": slug + ":floor", "extra": {}})
    arms.append({"arm": "OR-nitro", "host": "or", "model": slug + ":nitro", "extra": {}})
    return arms


async def call_once(client: AsyncOpenAI, name: str, arm: dict, kind: str, max_tokens: int) -> dict:
    rid = str(uuid.uuid4())
    nonce = secrets.token_hex(8)
    content = f"ID: {nonce}\n" + (TOOL_PROMPT if kind == "tool" else PROMPT)
    extra = dict(arm["extra"])
    if arm["host"] == "or":
        extra["trace"] = {"trace_id": rid}
    kwargs = dict(model=arm["model"], messages=[{"role": "user", "content": content}], stream=True,
                  stream_options={"include_usage": True}, temperature=0.6, top_p=1, max_tokens=max_tokens)
    if kind == "tool":
        kwargs.update(tools=TOOLS, tool_choice="auto")
    rec = {"request_id": rid, "model_key": name, "arm": arm["arm"], "host": arm["host"], "kind": kind,
           "model": arm["model"], "provider_req": arm["extra"].get("provider"), "ok": False}
    t0 = time.perf_counter_ns()
    first_any = first_content = last = None
    n_chunks = 0
    generated_times = []
    stream_end = None
    text_parts, tool_args = [], []
    reasoning_chars = 0
    usage = finish = resp_id = None
    try:
        # httpx.Timeout은 연결·읽기 단계별 제한이라 요청 전체 시간을 보장하지 않으므로, 요청 전체를 마감으로 감싼다.
        async with asyncio.timeout(REQUEST_DEADLINE_S):
            stream = await client.chat.completions.create(**kwargs, extra_body=extra or None)
            async for ch in stream:
                now = time.perf_counter_ns()
                n_chunks += 1
                resp_id = resp_id or getattr(ch, "id", None)
                if getattr(ch, "usage", None):
                    usage = ch.usage.model_dump()
                for c in ch.choices or []:
                    d = c.delta
                    dd = d.model_dump() if d else {}
                    has_reason = bool(dd.get("reasoning") or dd.get("reasoning_content"))
                    has_content = bool(dd.get("content"))
                    has_tool = bool(dd.get("tool_calls"))
                    if has_reason or has_content or has_tool:
                        generated_times.append(now / 1e9)
                    if (has_reason or has_content or has_tool) and first_any is None:
                        first_any = now
                    if (has_content or has_tool) and first_content is None:
                        first_content = now
                    if has_reason:
                        reasoning_chars += len(dd.get("reasoning") or dd.get("reasoning_content") or "")
                    if has_content:
                        text_parts.append(dd["content"])
                    if has_tool:
                        for tc in dd["tool_calls"]:
                            fn = (tc or {}).get("function") or {}
                            if fn.get("arguments"):
                                tool_args.append(fn["arguments"])
                    if c.finish_reason:
                        finish = c.finish_reason
                last = now
            stream_end = time.perf_counter_ns()
            rec["ok"] = finish not in (None, "length") and (kind != "tool" or bool(tool_args))
    except TimeoutError:
        rec["error"] = f"DeadlineExceeded: 요청 전체 마감 {REQUEST_DEADLINE_S}초 초과"
        rec["status"] = None
    except Exception as e:  # 실패도 결과로 기록
        rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        rec["status"] = getattr(e, "status_code", None)
    ms = lambda t: None if t is None else round((t - t0) / 1e6, 1)
    rec.update(resp_id=resp_id, finish_reason=finish, chunks=n_chunks, ttft_any_ms=ms(first_any),
               ttft_content_ms=ms(first_content), e2e_ms=ms(stream_end), usage=usage,
               text_head="".join(text_parts)[:160], tool_args="".join(tool_args)[:160],
               reasoning_chars=reasoning_chars,
               reasoning_tokens=((usage or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
               if ((usage or {}).get("completion_tokens_details") or {}).get("reasoning_tokens") is not None
               else (usage or {}).get("reasoning_tokens"))
    rec["generated_chunk_times_s"] = generated_times
    rec["last_generated_ms"] = None if not generated_times else round((generated_times[-1] - t0/1e9)*1000, 1)
    rec["decode_tps"] = decode_rate((usage or {}).get("completion_tokens"),
        rec["reasoning_tokens"], reasoning_chars > 0, generated_times) if rec["ok"] else None
    rec["decode_observable"] = rec["decode_tps"] is not None
    rec["total_tps"] = ((usage or {}).get("completion_tokens", 0) / ((stream_end-t0)/1e9)
        if rec["ok"] and stream_end and usage else None)
    return rec


async def fetch_generation(http: httpx.AsyncClient, key: str, gen_id: str) -> dict:
    for _ in range(5):
        r = await http.get(f"{OR_BASE}/generation", params={"id": gen_id}, headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 200:
            d = r.json().get("data", {})
            return {k: d.get(k) for k in ("provider_name", "total_cost", "latency", "generation_time",
                                          "native_tokens_prompt", "native_tokens_completion",
                                          "native_tokens_reasoning", "native_tokens_cached", "service_tier")} | {
                "attempts": [(p.get("provider_name"), p.get("status")) for p in (d.get("provider_responses") or [])],
                "provider_responses": d.get("provider_responses"), "permaslug": d.get("model")}
        await asyncio.sleep(6)
    return {"generation_error": r.status_code}


def pick_mango_id(ids: list[str], model: dict) -> str | None:
    cands = [i for i in ids if all(h in i.lower() for h in model["mango_hint"])
             and not any(x in i.lower() for x in model.get("mango_exclude", []))]
    return cands[0] if len(cands) == 1 else None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-mango-models", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--arms", default="", help="쉼표로 구분한 경로 이름만 실행 (예: M,OR-default)")
    ap.add_argument("--mango-id", action="append", default=[], help="model_key=mango_model_id (자동 매칭 덮어쓰기)")
    args = ap.parse_args()

    keys = load_keys()
    missing = [k for k, v in keys.items() if not v]
    if missing and not args.dry_run:
        sys.exit(f"키 없음: {', '.join(missing)} (환경변수 또는 %USERPROFILE%\\.mango_bench.env)")

    mango_ids: list[str] = []
    if keys["MANGO_API_KEY"]:
        async with httpx.AsyncClient(timeout=30) as h:
            r = await h.get(f"{MANGO_BASE}/models", headers={"Authorization": f"Bearer {keys['MANGO_API_KEY']}"})
            r.raise_for_status()
            mango_ids = [m["id"] for m in r.json().get("data", [])]
    if args.list_mango_models:
        print("\n".join(mango_ids))
        return

    overrides = dict(x.split("=", 1) for x in args.mango_id)
    plan = []
    for name, m in MODELS.items():
        mid = overrides.get(name) or pick_mango_id(mango_ids, m)
        for arm in arms_for(m, mid):
            plan.append((name, arm, "text"))
        for arm in arms_for(m, mid)[:2]:  # 도구 호출은 M, OR-default만
            plan.append((name, arm, "tool"))
    if args.arms:
        keep = set(args.arms.split(","))
        plan = [x for x in plan if x[1]["arm"] in keep]
    print(f"요청 {len(plan)}건")
    for name, arm, kind in plan:
        flag = "" if arm["model"] else "  <- Mango 모델 ID 미확정"
        print(f"  {name:26} {arm['arm']:24} {kind:4} {arm['model']}{flag}")
    if args.dry_run:
        return
    if any(not arm["model"] for _, arm, _ in plan):
        sys.exit("Mango 모델 ID를 확정하지 못했습니다. --list-mango-models로 확인 후 --mango-id로 지정하세요.")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    clients = {
        "mango": AsyncOpenAI(base_url=MANGO_BASE, api_key=keys["MANGO_API_KEY"], max_retries=0,
                             http_client=httpx.AsyncClient(timeout=httpx.Timeout(300.0))),
        "or": AsyncOpenAI(base_url=OR_BASE, api_key=keys["OPENROUTER_API_KEY"], max_retries=0,
                          http_client=httpx.AsyncClient(timeout=httpx.Timeout(300.0))),
    }
    sem = asyncio.Semaphore(4)

    async def run(item):
        name, arm, kind = item
        async with sem:
            return await call_once(clients[arm["host"]], name, arm, kind, args.max_tokens)

    recs = await asyncio.gather(*(run(i) for i in plan))
    await asyncio.sleep(10)
    async with httpx.AsyncClient(timeout=30) as h:
        for r in recs:
            if r["host"] == "or" and r.get("resp_id"):
                r["generation"] = await fetch_generation(h, keys["OPENROUTER_API_KEY"], r["resp_id"])

    with open(out / "raw.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    cols = ["model_key", "arm", "kind", "ok", "status", "finish_reason", "ttft_any_ms", "ttft_content_ms", "e2e_ms",
            "prompt_tokens", "completion_tokens", "reasoning_tokens", "served_by", "attempts", "cost", "error"]
    with open(out / "summary.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in recs:
            u = r.get("usage") or {}
            g = r.get("generation") or {}
            w.writerow({**{k: r.get(k) for k in cols}, "prompt_tokens": u.get("prompt_tokens"),
                        "completion_tokens": u.get("completion_tokens"),
                        "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                        "served_by": g.get("provider_name") or ("Mango" if r["host"] == "mango" else None),
                        "attempts": g.get("attempts"), "cost": g.get("total_cost") or u.get("cost")})
    ok = sum(r["ok"] for r in recs)
    print(f"완료: 성공 {ok}/{len(recs)} → {out}")


if __name__ == "__main__":
    asyncio.run(main())
