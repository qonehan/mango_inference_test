"""reasoning 설정 시험: 각 엔드포인트가 reasoning 켜기·끄기 지정을 실제로 따르는지 확인한다.

- Mango: 공식 문서상 reasoning 조절 파라미터가 없음. 문서에 없는 필드는 모델 서버로 그대로 전달된다고 하므로
  흔히 쓰는 형식(chat_template_kwargs, reasoning_effort)이 먹히는지 시험한다.
- OpenRouter: 통합 파라미터 reasoning={"enabled": true/false}. 고정은 제공사가 아니라 구체 엔드포인트 슬러그로 한다.
판정 근거는 응답 usage의 reasoning 토큰 수와 스트림으로 받은 reasoning 글자 수.

사용: python reasoning_param_test.py --out <폴더> [--reps 2]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

import httpx
from openai import AsyncOpenAI

import smoke_test as st

st.PROMPT = ("A train travels 60 km in 45 minutes. What is its average speed in km/h? "
             "Reply with the number only.")

MANGO_IDS = {
    "deepseek-v4.1-flash-1017": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
    "glm-5.3": "zai-org/GLM-5.3",
    "glm-5.3-flash": "zai-org/GLM-5.3-Flash",
    "kimi-k3.1-pro": "moonshotai/Kimi-K3.1-Pro",
}
OR_SLUGS = {k: v["or_slug"] for k, v in st.MODELS.items()}
# 간이 테스트에서 각 제공사 고정 시 실제로 응답한 엔드포인트 (구체 슬러그로 고정)
OR_ENDPOINTS = {
    "deepseek-v4.1-flash-1017": ["deepseek", "deepinfra/fp8", "together", "inference-net/us", "wafer/us"],
    "glm-5.3": ["wafer/us", "inference-net/us", "z-ai/fp8", "siliconflow/fp8", "decart/fp4"],
    "glm-5.3-flash": ["together", "inference-net/us", "open-inference/fp4", "coreweave/nvfp4", "z-ai/fp8"],
    "kimi-k3.1-pro": ["moonshotai/mxfp4", "inference-net/mxfp4", "modal/mxfp4", "wafer/us", "together"],
}
MANGO_VARIANTS = {
    "default": {},
    "off-template": {"chat_template_kwargs": {"enable_thinking": False, "thinking": False}},
    "on-template": {"chat_template_kwargs": {"enable_thinking": True, "thinking": True}},
    "effort-low": {"reasoning_effort": "low"},
}
OR_VARIANTS = {
    "default": {},
    "off": {"reasoning": {"enabled": False}},
    "on": {"reasoning": {"enabled": True}},
    "low": {"reasoning": {"effort": "low"}},
}


def plan(hosts: set[str], variants: set[str] | None) -> list[tuple[str, dict, str]]:
    items = []
    for mk in MANGO_IDS:
        for vn, vb in MANGO_VARIANTS.items():
            if "mango" not in hosts or (variants and vn not in variants):
                continue
            items.append((mk, {"arm": f"M|{vn}", "host": "mango", "model": MANGO_IDS[mk], "extra": dict(vb)}, vn))
        for ep in OR_ENDPOINTS[mk]:
            for vn, vb in OR_VARIANTS.items():
                if "or" not in hosts or (variants and vn not in variants):
                    continue
                extra = {"provider": {"order": [ep], "allow_fallbacks": False}, **vb}
                items.append((mk, {"arm": f"OR:{ep}|{vn}", "host": "or", "model": OR_SLUGS[mk], "extra": extra}, vn))
    return items


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--hosts", default="mango,or", help="mango, or 중 실행할 대상")
    ap.add_argument("--variants", default="", help="실행할 설정 이름만 (예: low). 비우면 전체")
    args = ap.parse_args()
    keys = st.load_keys()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    clients = {
        "mango": AsyncOpenAI(base_url=st.MANGO_BASE, api_key=keys["MANGO_API_KEY"], max_retries=0,
                             http_client=httpx.AsyncClient(timeout=httpx.Timeout(300.0))),
        "or": AsyncOpenAI(base_url=st.OR_BASE, api_key=keys["OPENROUTER_API_KEY"], max_retries=0,
                          http_client=httpx.AsyncClient(timeout=httpx.Timeout(300.0))),
    }
    items = [(mk, arm, vn, rep) for mk, arm, vn in plan(set(args.hosts.split(",")), set(args.variants.split(",")) if args.variants else None)
             for rep in range(args.reps)]
    print(f"요청 {len(items)}건")
    sem = asyncio.Semaphore(4)  # OpenRouter 새 계정 한도(모델당 분당 20회) 고려

    async def run(it):
        mk, arm, vn, rep = it
        async with sem:
            r = await st.call_once(clients[arm["host"]], mk, arm, "text", args.max_tokens)
            r.update(variant=vn, rep=rep)
            return r

    recs = await asyncio.gather(*(run(i) for i in items))
    await asyncio.sleep(10)
    async with httpx.AsyncClient(timeout=30) as h:
        for r in recs:
            if r["host"] == "or" and r.get("resp_id"):
                r["generation"] = await st.fetch_generation(h, keys["OPENROUTER_API_KEY"], r["resp_id"])
    with open(out / "raw.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    cols = ["model_key", "arm", "variant", "rep", "ok", "status", "finish_reason", "reasoning_tokens",
            "reasoning_chars", "completion_tokens", "ttft_content_ms", "answer", "served_by", "error"]
    with open(out / "summary.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in recs:
            g = r.get("generation") or {}
            w.writerow({**{k: r.get(k) for k in cols}, "completion_tokens": (r.get("usage") or {}).get("completion_tokens"),
                        "answer": (r.get("text_head") or "").strip()[:40],
                        "served_by": g.get("provider_name") or ("Mango" if r["host"] == "mango" else None),
                        "error": (r.get("error") or "")[:160]})
    print(f"완료: 성공 {sum(r['ok'] for r in recs)}/{len(recs)} → {out}")


if __name__ == "__main__":
    asyncio.run(main())
