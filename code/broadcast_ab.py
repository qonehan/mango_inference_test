"""파일럿 Broadcast 켬·끔 대조 (프로토콜 11절). 설정을 바꾸지 않고 API 키 두 개로 나눈다.

OpenRouter Broadcast는 API 키별 포함·제외 필터를 지원한다. 키 A(OPENROUTER_API_KEY)는 Broadcast 포함,
키 B(OPENROUTER_API_KEY_NOBC)는 Broadcast 제외로 설정해 둔다(사용자가 Settings에서 설정).
모델마다 OR-기본 W1 20개 질문을 두 키로 연속해서 보내고(순서 무작위), 첫 답변 시간의 기하평균 비율
(켬 ÷ 끔)과 95% 부트스트랩 구간을 기술 통계로 기록한다. 구간 하한이 1.10을 넘으면 동결 전에 원인 확인.

사용: python broadcast_ab.py --config config/pilot.yaml --prompts prompts --out runs/pilot/broadcast_ab
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
from pathlib import Path

from bench_common import (RateLimiter, arms_for, load_config, load_keys, make_clients, nonce_hex, stream_call,
                          with_nonce)
from runner import load_prompts


async def main(a) -> None:
    cfg = load_config(a.config)
    P = load_prompts(Path(a.prompts))
    keys = load_keys()
    kb = os.environ.get("OPENROUTER_API_KEY_NOBC")
    if not kb:
        raise SystemExit("OPENROUTER_API_KEY_NOBC(Broadcast 제외 키)가 필요합니다")
    on = make_clients(cfg, keys)["or"]
    off = make_clients(cfg, {**keys, "OPENROUTER_API_KEY": kb})["or"]
    n = cfg.get("broadcast_ab_pairs", 20)
    rng = random.Random(cfg["seed"] + 77)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    f = open(out / "raw.jsonl", "a", encoding="utf-8")
    summary = {}

    async def model_lane(mk):
        arm = arms_for(cfg, mk)["OR-default"]
        m = cfg["models"][mk]
        rate = RateLimiter(cfg["limits"]["openrouter_rpm_per_model"])
        pairs = []
        for it in rng.sample(P["w1"], n):
            res = {}
            for label in rng.sample(["on", "off"], 2):
                await rate.acquire()
                rec = await stream_call(on if label == "on" else off, host="or", model=arm["model"],
                                        messages=with_nonce(it["messages"], nonce_hex()),
                                        max_tokens=cfg["max_tokens_cap"], temperature=m["temperature"],
                                        top_p=m["top_p"], deadline_s=cfg["request_deadline_s"],
                                        meta={"task": "BROADCAST_AB", "model_key": mk, "broadcast": label,
                                              "item_id": it["id"]})
                rec.pop("chunks", None)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                res[label] = rec
            if all(res[x]["ok"] and res[x]["ttft_content_ms"] for x in ("on", "off")):
                pairs.append(math.log(res["on"]["ttft_content_ms"] / res["off"]["ttft_content_ms"]))
        boots = sorted(sum(random.Random(i).choices(pairs, k=len(pairs))) / len(pairs) for i in range(10000)) if pairs else []
        summary[mk] = {"pairs": len(pairs), "ratio_on_off": math.exp(sum(pairs) / len(pairs)) if pairs else None,
                       "ci95": [math.exp(boots[249]), math.exp(boots[9749])] if boots else None}
        summary[mk]["check_needed"] = bool(summary[mk]["ci95"] and summary[mk]["ci95"][0] > 1.10)

    await asyncio.gather(*(model_lane(mk) for mk in cfg["models"]))
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--prompts", required=True)
    ap.add_argument("--out", required=True)
    asyncio.run(main(ap.parse_args()))
