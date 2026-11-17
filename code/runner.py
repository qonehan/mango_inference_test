"""회차 1회 실행기 (프로토콜 v0.9.4 5-2·5-3·5-4절, 부록 A-4).

구성 (모델마다):
- 주 레인: M·OR-기본 주 비교 블록(공통 W1 쌍, 공통 W3 쌍, W2 세션 쌍)을 순서대로 실행하고,
  블록 사이에 M·OR-기본 탐색 요청(OR-기본 추가 W1·W3 60건씩, L, Z, M 캐시 대조)을 고르게 끼운다.
  블록 안의 두 경로 순서는 시드로 무작위화하고 연속 실행한다. W2는 한 경로 10턴을 끝낸 뒤 다른 경로 10턴.
- 보조 레인: OR-고정 5곳·정렬/접미사 5경로마다 하나. Mango를 쓰지 않는다.
- 본 루프 뒤 부하 조건: M·OR-기본 W1 24건을 동시 4건 × 6묶음 (그동안 다른 Mango 호출 없음).
한도: 전체 동시 16, Mango 합계 동시 3·분당 36, OpenRouter 모델별 분당 18.
OpenRouter 계정 한도 429는 우리 쪽 실패로 기록하고 회차 끝에 한 번 다시 보낸다.
회차 마감(다음 회차 시작) 전에 보내지 못한 요청은 미측정으로 기록한다.

사용:
  python runner.py --config config/day1.yaml --prompts prompts --slot 1 --out runs/2026-11-18/S1
  python runner.py ... --dry-run            # 호출 없이 계획만 (건수 확인)
  python runner.py ... --mango-base http://127.0.0.1:8099/v1 --or-base http://127.0.0.1:8099/v1  # 모의 서버
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bench_common import (Limits, arms_for, file_sha256, load_config, load_keys, make_clients, nonce_hex,
                          slot_seed, stream_call, with_nonce)
from build_prompts import w2_messages


def read_jsonl(p: Path) -> list:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


class Writer:
    def __init__(self, path: Path):
        self.f = open(path, "a", encoding="utf-8")
        self.n = Counter()

    def write(self, rec: dict) -> None:
        self.f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.f.flush()
        self.n["ok" if rec.get("ok") else (rec.get("fail_side") or rec.get("status_note") or "fail")] += 1


# ---------------------------------------------------------------- 계획

def make_plan(cfg: dict, P: dict, slot: str, models: list[str]) -> dict:
    C = cfg["counts"]
    seed = slot_seed(cfg, slot)
    rng = random.Random(seed)
    w1_s = rng.sample(P["w1"], max(C["or_default_w1"], C["common_w1"]))
    w3_s = rng.sample(P["w3"], max(C["or_default_w3"], C["common_w3"]))
    pool = P["w2_pilot"] if cfg.get("pilot") else P["w2_main"]
    k = C["w2_sessions_per_slot"]
    if cfg.get("pilot"):
        w2_s = pool[:k]
    else:  # 회차별로 겹치지 않는 세션 묶음 (회차 사이 재사용 없음)
        order = pool[:]
        random.Random(cfg["seed"]).shuffle(order)
        idx = int(slot) - 1
        w2_s = order[idx * k:(idx + 1) * k]
        if len(w2_s) < k:
            sys.exit("W2 세션 풀이 부족합니다")
    c_s = rng.sample(w2_s, min(C["cache_pairs"], len(w2_s)))
    l_s = [it for L in (1000, 10000, 32000)
           for it in rng.sample([x for x in P["l"] if x["length"] == L], C["l_per_length"])]
    load = w1_s[:C["load"]]
    plan = {"slot": slot, "seed": seed, "models": {}}
    for mi, mk in enumerate(models):
        arms = arms_for(cfg, mk)
        r = random.Random(seed + 1000 * (mi + 1))
        blocks = []
        for it in w1_s[:C["common_w1"]]:
            blocks.append(("W1", it))
        for it in w3_s[:C["common_w3"]]:
            blocks.append(("W3", it))
        for s in w2_s:
            blocks.append(("W2", s))
        r.shuffle(blocks)
        blocks = [{"block_id": f"{slot}-{mk}-b{j:03d}", "task": t, "item": it,
                   "order": r.sample(["M", "OR-default"], 2)} for j, (t, it) in enumerate(blocks)]
        extras = ([("OR-default", "W1", it) for it in w1_s[C["common_w1"]:C["or_default_w1"]]]
                  + [("OR-default", "W3", it) for it in w3_s[C["common_w3"]:C["or_default_w3"]]]
                  + [(a, "L", it) for a in ("M", "OR-default") for it in l_s]
                  + [(a, "Z", P["z"][0]) for a in ("M", "OR-default") for _ in range(C["z"])]
                  + [("M", "CACHE", s) for s in c_s])
        r.shuffle(extras)
        side = {}
        for p in cfg["models"][mk]["pins"]:
            a = f"OR-pin:{p}"
            items = ([(a, "W1", it) for it in w1_s[:C["common_w1"]]] + [(a, "W3", it) for it in w3_s[:C["common_w3"]]]
                     + [(a, "W2", s) for s in w2_s[:C["pin_w2_sessions"]]] + [(a, "L", it) for it in l_s]
                     + [(a, "Z", P["z"][0]) for _ in range(C["z"])] + [(a, "CACHE", s) for s in c_s])
            r.shuffle(items)
            side[a] = items
        for a in [x for x in arms if x.startswith(("OR-sort", "OR-floor", "OR-nitro"))]:
            items = [(a, "W1", it) for it in w1_s[:C["sort_w1"]]] + [(a, "W3", it) for it in w3_s[:C["sort_w3"]]]
            r.shuffle(items)
            side[a] = items
        plan["models"][mk] = {"arms": arms, "blocks": blocks, "extras": extras, "side": side,
                              "load": [("M", "W1", it) for it in load] + [("OR-default", "W1", it) for it in load]}
    return plan


def count_requests(plan: dict, cfg: dict) -> Counter:
    c = Counter()
    for mk, m in plan["models"].items():
        for b in m["blocks"]:
            for a in b["order"]:
                c[(mk, a)] += 10 if b["task"] == "W2" else 1
        for lst in [m["extras"], m["load"]] + list(m["side"].values()):
            for a, t, _ in lst:
                c[(mk, a)] += {"W2": 10, "CACHE": 2}.get(t, 1)
    return c


# ---------------------------------------------------------------- 실행

class Runner:
    def __init__(self, cfg, P, plan, clients, writer, deadline: float | None, mango_conc=None):
        self.cfg, self.P, self.plan, self.clients, self.w = cfg, P, plan, clients, writer
        self.lim = Limits(cfg, mango_conc)
        self.deadline = deadline
        self.retry_queue: list = []
        self.tools = P["tools"]

    def past_deadline(self) -> bool:
        return self.deadline is not None and time.time() >= self.deadline

    def unmeasured(self, meta: dict, n: int = 1) -> None:
        for _ in range(n):
            self.w.write({**meta, "request_id": str(uuid.uuid4()), "ok": False, "status_note": "unmeasured",
                          "fail_side": None, "error": "slot_deadline_not_sent", "t_start_utc": time.time()})

    async def one(self, mk: str, arm_name: str, task: str, messages: list, meta: dict, *, tools=False,
                  need_tool=False, max_tokens=None, allow_retry=True) -> dict:
        arm = self.plan["models"][mk]["arms"][arm_name]
        m = self.cfg["models"][mk]
        meta = {"slot": self.plan["slot"], "model_key": mk, "arm": arm_name, "task": task,
                "pilot": bool(self.cfg.get("pilot")), **meta}
        if self.past_deadline():
            self.unmeasured(meta)
            return {"ok": False, "status_note": "unmeasured"}
        async with self.lim.ctx(arm["host"], mk):
            rec = await stream_call(self.clients[arm["host"]], host=arm["host"], model=arm["model"],
                                    messages=messages, max_tokens=max_tokens or self.cfg["max_tokens_cap"],
                                    temperature=m["temperature"], top_p=m["top_p"], extra=arm["extra"],
                                    tools=self.tools if tools else None, need_tool=need_tool,
                                    deadline_s=self.cfg["request_deadline_s"], meta=meta)
        self.w.write(rec)
        if allow_retry and rec.get("error") == "or_account_rate_limit":
            self.retry_queue.append((mk, arm_name, task, messages, {**meta, "resent_of": rec["request_id"]},
                                     tools, need_tool, max_tokens))
        return rec

    async def run_item(self, mk, arm_name, task, item, extra_meta=None):
        meta = {"item_id": item.get("id"), **(extra_meta or {})}
        if task in ("W1", "W3"):
            await self.one(mk, arm_name, task, with_nonce(item["messages"], nonce_hex()), meta,
                           tools=task == "W3", need_tool=task == "W3")
        elif task == "L":
            await self.one(mk, arm_name, task, with_nonce(item["messages"], nonce_hex()),
                       {**meta, "length": item["length"]}, max_tokens=64)
        elif task == "Z":
            await self.one(mk, arm_name, task, with_nonce(item["messages"], nonce_hex()), meta, max_tokens=1)
        elif task == "W2":
            await self.session(mk, arm_name, item, meta)
        elif task == "CACHE":
            msgs = w2_messages(item, 6)
            n = nonce_hex()
            await self.one(mk, arm_name, task, with_nonce(msgs, n), {**meta, "cache_leg": 1, "cache_pair": n},
                           tools=True, need_tool=True)
            await self.one(mk, arm_name, task, with_nonce(msgs, n), {**meta, "cache_leg": 2, "cache_pair": n},
                           tools=True, need_tool=True)

    async def session(self, mk, arm_name, sess, meta):
        n = nonce_hex()
        sid = f"{self.plan['slot']}-{mk}-{arm_name}-{sess['id']}"
        for k in range(1, len(sess["turns"]) + 1):
            rec = await self.one(mk, arm_name, "W2", with_nonce(w2_messages(sess, k), n),
                                 {**meta, "session_id": sid, "turn": k}, tools=True, need_tool=True,
                                 allow_retry=False)
            if not rec.get("ok"):  # 한 턴이 실패하면 나머지 턴은 보내지 않는다 (P4 규칙)
                left = len(sess["turns"]) - k
                for kk in range(k + 1, k + 1 + left):
                    self.w.write({"slot": self.plan["slot"], "model_key": mk, "arm": arm_name, "task": "W2",
                                  **meta, "session_id": sid, "turn": kk, "request_id": str(uuid.uuid4()),
                                  "ok": False, "status_note": "session_stopped", "t_start_utc": time.time()})
                return

    async def main_lane(self, mk):
        m = self.plan["models"][mk]
        blocks, extras = m["blocks"], list(m["extras"])
        per_gap = len(extras) / max(1, len(blocks))
        acc = 0.0
        for b in blocks:
            for pos, a in enumerate(b["order"]):
                await self.run_item(mk, a, b["task"], b["item"],
                                    {"block_id": b["block_id"], "block_pos": pos, "primary": True})
            acc += per_gap
            while acc >= 1 and extras:
                a, t, it = extras.pop(0)
                await self.run_item(mk, a, t, it)
                acc -= 1
        for a, t, it in extras:
            await self.run_item(mk, a, t, it)

    async def side_lane(self, mk, items):
        for a, t, it in items:
            await self.run_item(mk, a, t, it)

    async def warmup(self, mk, arm_name):
        await self.one(mk, arm_name, "WARMUP", with_nonce(self.P["z"][0]["messages"], nonce_hex()),
                       {"warmup": True}, max_tokens=1, allow_retry=False)

    async def load_phase(self):
        conc = self.cfg["limits"]["load_concurrency"]
        self.lim = Limits(self.cfg, mango_concurrency=conc)
        for mk, m in self.plan["models"].items():
            for arm_name in ("M", "OR-default"):
                items = [it for a, _, it in m["load"] if a == arm_name]
                for i in range(0, len(items), conc):
                    await asyncio.gather(*(self.one(mk, arm_name, "LOAD", with_nonce(it["messages"], nonce_hex()),
                                                    {"item_id": it["id"], "load_batch": i // conc})
                                           for it in items[i:i + conc]))

    async def run(self, do_load=True):
        models = list(self.plan["models"])
        await asyncio.gather(*(self.warmup(mk, a) for mk in models for a in self.plan["models"][mk]["arms"]))
        lanes = [self.main_lane(mk) for mk in models]
        lanes += [self.side_lane(mk, items) for mk in models for items in self.plan["models"][mk]["side"].values()]
        await asyncio.gather(*lanes)
        if do_load:
            await self.load_phase()
        queue, self.retry_queue = self.retry_queue, []
        for mk, a, t, msgs, meta, tools, need_tool, mt in queue:
            await self.one(mk, a, t, msgs, meta, tools=tools, need_tool=need_tool, max_tokens=mt, allow_retry=False)


def next_slot_deadline(cfg: dict) -> float:
    now = datetime.now(timezone.utc)
    starts = []
    for d in (0, 1):
        for hhmm in cfg["slots_utc"]:
            h, mnt = map(int, hhmm.split(":"))
            starts.append((now + timedelta(days=d)).replace(hour=h, minute=mnt, second=0, microsecond=0))
    future = sorted(s for s in starts if s > now + timedelta(minutes=30))
    return future[0].timestamp()


def load_prompts(d: Path) -> dict:
    P = {k: read_jsonl(d / f"{k}.jsonl") for k in ("w1", "w3", "w2_main", "w2_pilot", "l", "z")}
    P["tools"] = json.loads((d / "tools.json").read_text(encoding="utf-8"))
    return P


async def amain() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--prompts", required=True)
    ap.add_argument("--slot", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default="", help="쉼표로 구분 (기본: 전체)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-load", action="store_true")
    ap.add_argument("--no-control", action="store_true")
    ap.add_argument("--deadline-utc", default="", help="ISO 시각. 기본: 다음 회차 시작")
    ap.add_argument("--mango-base", default=None)
    ap.add_argument("--or-base", default=None)
    a = ap.parse_args()

    cfg = load_config(a.config)
    pdir = Path(a.prompts)
    P = load_prompts(pdir)
    models = a.models.split(",") if a.models else list(cfg["models"])
    plan = make_plan(cfg, P, a.slot, models)
    cnt = count_requests(plan, cfg)
    total = sum(cnt.values())
    print(f"회차 {a.slot}: 계획 {total}건 (모델 {len(models)}개, 준비 호출 제외)")
    for mk in models:
        row = {k[1]: v for k, v in cnt.items() if k[0] == mk}
        print(f"  {mk}: {sum(row.values())}건  " + ", ".join(f"{k} {v}" for k, v in row.items()))
    if a.dry_run:
        return

    keys = load_keys()
    if not a.mango_base and (not keys["MANGO_API_KEY"] or not keys["OPENROUTER_API_KEY"]):
        sys.exit("키 없음 (MANGO_API_KEY, OPENROUTER_API_KEY)")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    deadline = (datetime.fromisoformat(a.deadline_utc).timestamp() if a.deadline_utc
                else (None if a.mango_base else next_slot_deadline(cfg)))
    clients = make_clients(cfg, keys, a.mango_base, a.or_base)
    writer = Writer(out / "raw.jsonl")
    started = time.time()
    control = None
    if not a.no_control and not a.mango_base:
        from probe import control_loop
        control = asyncio.create_task(control_loop(cfg, out / "control.jsonl"))
    try:
        await Runner(cfg, P, plan, clients, writer, deadline).run(do_load=not a.no_load)
    finally:
        if control:
            control.cancel()
    manifest = {"slot": a.slot, "seed": plan["seed"], "config": a.config, "config_sha256": file_sha256(a.config),
                "prompts_manifest": json.loads((pdir / "manifest.json").read_text(encoding="utf-8")),
                "started_utc": started, "ended_utc": time.time(), "deadline_utc": deadline,
                "planned": total, "outcomes": dict(writer.n), "pilot": bool(cfg.get("pilot"))}
    mf = out / "manifest.json"
    old = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
    mf.write_text(json.dumps({**old, "run": manifest}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest["outcomes"], ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(amain())
