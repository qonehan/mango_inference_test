"""실험 2-2회 실행기 (2026-11-22). 설계: 02_분석/30번 3-1·3-2절.

명령
  prep   : 경로 점검(엔드포인트 존재·tools/response_format 지원), 단가 스냅숏, T·J 기준점 후보(공개 오류율) 출력
  round  : S·L·G 한 회차 (VM 두 곳이 같은 시각에 각자 실행)
  tj     : T·J (서울 VM만, 회차 사이 빈 시간에 --until-utc까지. 중단 후 다시 실행하면 이어서 함)
  gens   : OR generation(total_cost 등) 회수
  status : 건수·실패·비용 추정 요약

기록: <out>/records/<mode>_<id>.jsonl (요청 1건 = 1줄). 키 값은 기록하지 않는다.
측정 함수는 1회 실험 bench_common.stream_call(측정기 검증 완료)을 그대로 쓰고, on_chunk로 도구 인자·본문을
잘리지 않게 따로 모아 판정한다.

예(VM):
  cd /opt/mango-bench/exp2_2 && /opt/mango-bench/venv/bin/python runner_20261122.py prep --out run
  ... round --vm seoul --round-id r1 --out run --max-usd 30
  ... tj --out run --until-utc 05:30 --max-usd 60 --anchors deepseek-v4.1-flash-1017=deepseek,...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get("BENCH_CODE", "/opt/mango-bench/code"))
sys.path.insert(0, str(HERE))
import bench_common as BC  # noqa: E402
import judge_20261122 as J  # noqa: E402

MANGO_BASE = "https://api.mangoboost.io/v1"
OR_BASE = "https://openrouter.ai/api/v1"
UA = "mango-bench/1.0"
DEADLINE_S = 300

# 모델 설정(1회 실험 day1.yaml과 같은 표본 추출 값). mango_price: $/100만 토큰 (입력, 캐시 읽기, 출력), 17번 정가
MODELS = {
    "deepseek-v4.1-flash-1017": {"mango_id": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
                                 "or_slug": "deepseek/deepseek-v4.1-flash-1017",
                                 "permaslug": "deepseek/deepseek-v4.1-flash-20261017",
                                 "temperature": 1.0, "top_p": 1.0, "mango_price": (0.20, 0.006, 0.80)},
    "glm-5.3": {"mango_id": "zai-org/GLM-5.3", "or_slug": "z-ai/glm-5.3", "permaslug": "z-ai/glm-5.3-20260816",
                "temperature": 1.0, "top_p": 0.95, "mango_price": (1.30, 0.14, 4.20)},
    "glm-5.3-flash": {"mango_id": "zai-org/GLM-5.3-Flash", "or_slug": "z-ai/glm-5.3-flash",
                      "permaslug": "z-ai/glm-5.3-flash-20260826",
                      "temperature": 1.0, "top_p": 0.95, "mango_price": (0.13, 0.03, 0.45)},
    "kimi-k3.1-pro": {"mango_id": "moonshotai/Kimi-K3.1-Pro", "or_slug": "moonshotai/kimi-k3.1-pro",
                      "permaslug": "moonshotai/kimi-k3.1-pro-20261023",
                      "temperature": 1.0, "top_p": 0.95, "mango_price": (1.80, 0.19, 9.50)},
}
# OR 고정 경로(30번 3-1절, 2026-11-22 확정). 값 = 2-1회 39시간 배정 비율(%). base=True면 비교 기준값에 들어감
PATHS = {
    "deepseek-v4.1-flash-1017": {"wafer/us": 11.8, "wafer/mxfp4": 11.8, "inference-net/us": 11.0, "morph/fp8": 9.7,
                                 "deepinfra/fp8": 8.0, "open-inference/fp4": 0.4},
    "glm-5.3": {"wafer/fp4": 46.9, "wafer/us": 29.5, "decart/fp4": 3.2, "friendli": 3.0, "inference-net/fp4": 2.8,
                "parasail/fp4": 0.4},   # fireworks/spot은 제공사가 spot 등급을 거부(400)해 다음 순위로 교체(2026-11-22 시험)
    "glm-5.3-flash": {"wafer/nvfp4": 14.7, "wafer/us": 13.0, "inference-net/fp4": 10.5, "inference-net/us": 10.0,
                      "friendli": 6.5, "cohere/nvfp4": 0.5},
    "kimi-k3.1-pro": {"wafer/mxfp4": 12.6, "inference-net/mxfp4/us": 12.3, "wafer/us": 12.2,
                      "inference-net/mxfp4": 11.6, "river": 10.6, "modal/mxfp4": 10.3},
}
ROUND = {"S": 20, "L": 10, "G": 10, "warmup": {"mango": 3, "or": 1}, "mango_reps": 2,
         "max_tokens": {"S": 512, "L": 512, "G": 1024}}
# T·J 건수: 실제 시험의 요청당 비용(Kimi T 약 $0.015)으로 예산 $95 안에 맞춤(2026-11-22). 앵커는 지정한 종류만 OR 경로 건수
TJ = {"count": {"kimi-k3.1-pro": {"mango": {"T": 600, "J": 400}, "or": {"T": 100, "J": 60}},
                "glm-5.3": {"mango": {"T": 1000, "J": 600}, "or": {"T": 200, "J": 120}},
                "glm-5.3-flash": {"mango": {"T": 1500, "J": 1000}, "or": {"T": 300, "J": 200}},
                "deepseek-v4.1-flash-1017": {"mango": {"T": 1500, "J": 1000}, "or": {"T": 300, "J": 200}}},
      "max_tokens": 4096}

# 2-1회 긴 입력 형식 (exp2_1_long_input_control_20261121.py와 같음)
WORDS = ("river market signal engine harbor ledger window planet garden circuit lantern meadow "
         "copper thunder orbit canvas pillar velvet summit quartz anchor bridge castle desert "
         "forest glacier island jungle kettle ladder marble needle ocean pepper quiver ribbon "
         "saddle temple umbrella valley wagon yonder zephyr amber basin cedar dune ember fable "
         "grove hollow ivory jasper kernel lagoon mosaic nectar oasis prism quill rapids sierra "
         "tundra utopia vortex willow yarrow zenith").split()


def filler(seed: str, n: int = 2300) -> str:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        out.append(rnd.choice(WORDS))
        if i % 14 == 13:
            out[-1] += "."
    return " ".join(out)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def http_json(url: str, key: str | None = None, timeout: float = 30):
    h = {"User-Agent": UA}
    if key:
        h["Authorization"] = f"Bearer {key}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------- 경로·메시지

def all_paths() -> list[dict]:
    ps = []
    for mk, m in MODELS.items():
        ps.append({"pid": f"{mk}|mango", "mk": mk, "host": "mango", "model": m["mango_id"], "tag": "mango", "extra": {}})
        for tag in PATHS[mk]:
            ps.append({"pid": f"{mk}|{tag}", "mk": mk, "host": "or", "model": m["or_slug"], "tag": tag,
                       "extra": {"provider": {"order": [tag], "allow_fallbacks": False}}})
    return ps


def build_request(kind: str, path: dict, tag: str, item: dict | None) -> dict:
    """kind별 messages·max_tokens·tools·extra. tag는 요청 고유 번호."""
    extra = json.loads(json.dumps(path["extra"]))
    if kind in ("S", "warmup"):
        msgs = [{"role": "system", "content": f"Request {tag}. Answer in one short word."},
                {"role": "user", "content": f"[{tag}] Reply with the single word OK."}]
        return {"messages": msgs, "max_tokens": ROUND["max_tokens"]["S"], "extra": extra, "expect_length": True}
    if kind == "L":
        msgs = [{"role": "system", "content": f"Request {tag}. Background notes follow; ignore them.\n\n"
                                              f"{filler(tag)}\n\nAnswer in one short word."},
                {"role": "user", "content": f"[{tag}] Reply with the single word OK."}]
        return {"messages": msgs, "max_tokens": ROUND["max_tokens"]["L"], "extra": extra, "expect_length": True}
    if kind == "G":
        # 잘려도(length) 속도는 계산하므로 정상 종료로 둔다. 속도는 one_call에서 decode_tps_any로 따로 계산
        return {"messages": BC.with_nonce(item["messages"], tag), "max_tokens": ROUND["max_tokens"]["G"],
                "extra": extra, "expect_length": True}
    # T·J: 고유 번호를 맨 뒤에 붙여 앞부분(도구 정의·스키마·문맥)이 같은 과제끼리 캐시되게 함
    msgs = json.loads(json.dumps(item["messages"]))
    msgs[-1]["content"] += f"\n\n[request id: {tag}]"
    if path["host"] == "or":
        extra["provider"]["require_parameters"] = True
    if kind == "T":
        return {"messages": msgs, "max_tokens": TJ["max_tokens"], "extra": extra, "tools": item["tools"],
                "expect_length": False}
    extra["response_format"] = {"type": "json_schema",
                                "json_schema": {"name": item["schema_name"], "strict": True, "schema": item["schema"]}}
    return {"messages": msgs, "max_tokens": TJ["max_tokens"], "extra": extra, "expect_length": False}


# ---------------------------------------------------------------- 비용 추정·기록

class Ledger:
    def __init__(self, out: Path, prices: dict):
        self.out, self.prices = out, prices
        self.total = 0.0
        for f in (out / "records").glob("*.jsonl"):
            for line in f.open(encoding="utf-8"):
                try:
                    self.total += json.loads(line).get("cost_est") or 0.0
                except ValueError:
                    pass

    def cost(self, path: dict, rec: dict) -> float:
        p = rec.get("prompt_tokens") or 0
        c = rec.get("cached_tokens") or 0
        o = rec.get("completion_tokens") or 0
        if path["host"] == "mango":
            pin, pc, pout = (x / 1e6 for x in MODELS[path["mk"]]["mango_price"])
        else:
            pr = self.prices.get(path["pid"]) or {}
            pin, pout = pr.get("prompt") or 0.0, pr.get("completion") or 0.0
            pc = pr.get("input_cache_read") if pr.get("input_cache_read") is not None else pin
        return (p - c) * pin + c * pc + o * pout


class Writer:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = path.open("a", encoding="utf-8", newline="\n")

    def write(self, rec: dict) -> None:
        self.f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.f.flush()


async def one_call(clients, path, kind, item, meta, ledger: Ledger, writer: Writer, limiter=None) -> dict:
    tag = f"{meta['run']}-{uuid.uuid4().hex[:12]}"
    req = build_request(kind, path, tag, item)
    calls: dict = {}
    content: list = []

    def on_chunk(ch, now):
        for c in ch.choices or []:
            d = c.delta.model_dump() if c.delta else {}
            if d.get("content"):
                content.append(d["content"])
            for j, tc in enumerate(d.get("tool_calls") or []):
                tc = tc or {}
                fn = tc.get("function") or {}
                x = calls.setdefault(tc.get("index", j), {"name": None, "args": []})
                x["name"] = x["name"] or fn.get("name")
                if fn.get("arguments"):
                    x["args"].append(fn["arguments"])

    m = MODELS[path["mk"]]
    if limiter:
        await limiter.acquire()
    try:
        rec = await BC.stream_call(clients[path["host"]], host=path["host"], model=path["model"],
                                   messages=req["messages"], max_tokens=req["max_tokens"],
                                   temperature=m["temperature"], top_p=m["top_p"], extra=req["extra"],
                                   tools=req.get("tools"), need_tool=False, deadline_s=DEADLINE_S,
                                   meta={**meta, "kind": kind, "pid": path["pid"], "mk": path["mk"], "tag": path["tag"],
                                         "item_id": (item or {}).get("id"), "nonce": tag},
                                   on_chunk=on_chunk, keep_text=400, expect_length=req["expect_length"])
    finally:
        if limiter:
            limiter.release()
    call_list = [{"name": x["name"], "arguments": "".join(x["args"])} for _, x in sorted(calls.items())]
    if kind == "T":
        rec["judge"] = J.judge_t(call_list, "".join(content), rec.get("finish_reason"), item["tools"])
        rec["tool_calls_full"] = call_list
    elif kind == "J":
        rec["judge"] = J.judge_j("".join(content), rec.get("finish_reason"), item["schema"])
        rec["content_full"] = "".join(content)
    # 잘린 응답(length)까지 포함한 생성 속도. stream_call은 정상 종료만 decode_tps를 계산하므로 따로 둔다
    gen_times = [t / 1000 for t, k in (rec.get("chunks") or []) if k in "rct"]
    rec["decode_tps_any"] = (BC.decode_rate(rec.get("completion_tokens"), rec.get("reasoning_tokens"),
                                            (rec.get("reasoning_chars") or 0) > 0, gen_times)
                             if rec.get("completion_tokens") and len(gen_times) >= 2 else None)
    rec["cost_est"] = ledger.cost(path, rec)
    ledger.total += rec["cost_est"]
    writer.write(rec)
    return rec


class MangoGate:
    """동시 수·분당 수 제한. Mango(두 VM이 한 계정 한도를 나눠 씀)와 OR 모델별 계정 한도(새 계정 분당 20건)에 씀."""

    def __init__(self, conc: int, rpm: int):
        self.sem, self.rate = asyncio.Semaphore(conc), BC.RateLimiter(rpm)

    async def acquire(self):
        await self.sem.acquire()
        await self.rate.acquire()

    def release(self):
        self.sem.release()


# ---------------------------------------------------------------- prep

def cmd_prep(a) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    keys = BC.load_keys()
    snap = {"utc": utcnow().isoformat(), "endpoints": {}, "mango_models": None, "errors": {}}
    prices, problems = {}, []
    for mk, m in MODELS.items():
        d = http_json(f"{OR_BASE}/models/{m['or_slug']}/endpoints")
        eps = d["data"]["endpoints"]
        snap["endpoints"][mk] = eps
        by_tag = {e.get("tag"): e for e in eps}
        for tag in PATHS[mk]:
            e = by_tag.get(tag)
            pid = f"{mk}|{tag}"
            if not e:
                problems.append(f"{pid}: 엔드포인트 목록에 없음")
                continue
            sp = set(e.get("supported_parameters") or [])
            pr = e.get("pricing") or {}
            prices[pid] = {k: float(pr[k]) for k in ("prompt", "completion", "input_cache_read") if pr.get(k) not in (None, "")}
            for need in ("tools", "response_format"):
                if need not in sp and not (need == "response_format" and "structured_outputs" in sp):
                    problems.append(f"{pid}: {need} 미지원")
            if "structured_outputs" not in sp:
                problems.append(f"{pid}: structured_outputs 표시 없음(strict json_schema 보장 불확실)")
            if e.get("status") not in (0, None):
                problems.append(f"{pid}: status={e.get('status')}")
        q = f"permaslug={m['permaslug']}&variant=standard&timeRange=1w"
        try:  # 엔드포인트 id → 태그 (오류율 API는 id로만 나옴)
            st = http_json(f"https://openrouter.ai/api/frontend/v1/stats/endpoint?permaslug={m['permaslug']}&variant=standard")
            snap.setdefault("id2tag", {}).update({x["id"]: x.get("provider_slug") for x in st.get("data") or []})
        except Exception as e:  # noqa: BLE001
            problems.append(f"{mk} stats/endpoint: {e}")
        for api in ("tool-call-error-rate", "structured-output-error-rate"):
            try:
                snap["errors"].setdefault(mk, {})[api] = http_json(f"https://openrouter.ai/api/frontend/v1/stats/{api}?{q}")
            except Exception as e:  # noqa: BLE001
                problems.append(f"{mk} {api}: {e}")
    try:
        snap["mango_models"] = http_json(f"{MANGO_BASE}/models", keys.get("MANGO_API_KEY"))
    except Exception as e:  # noqa: BLE001
        problems.append(f"Mango /models: {e}")
    (out / "prep_snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    (out / "prices.json").write_text(json.dumps(prices, indent=1), encoding="utf-8")
    print("문제:" if problems else "문제 없음")
    for p in problems:
        print("  -", p)
    # 기준점 후보: 1주 도구 호출 오류율 상위 (요청 수 가중)
    for mk, e in snap["errors"].items():
        for api, body in e.items():
            rows = (body or {}).get("data") or []
            tot_err, tot_vol = Counter(), Counter()
            for r in rows:
                for ep, y in (r.get("y") or {}).items():
                    v = (r.get("volume") or {}).get(ep) or 0
                    if y is not None and v:
                        tot_err[ep] += float(y) * v
                        tot_vol[ep] += v
            top = sorted(((tot_err[k] / tot_vol[k], tot_vol[k], k) for k in tot_vol if tot_vol[k] >= 1000), reverse=True)[:5]
            id2tag = snap.get("id2tag", {})
            print(f"{mk} {api} 상위(오류율%, 1주 요청 수, 태그): "
                  f"{[(round(x, 2), int(v), id2tag.get(k, k)) for x, v, k in top]}")


# ---------------------------------------------------------------- round (S·L·G)

def load_w1() -> list[dict]:
    p = Path(os.environ.get("W1_PATH", "/opt/mango-bench/prompts/w1.jsonl"))
    return [json.loads(x) for x in p.open(encoding="utf-8")]


async def cmd_round(a) -> None:
    out = Path(a.out)
    prices = json.loads((out / "prices.json").read_text())
    ledger = Ledger(out, prices)
    keys = BC.load_keys()
    cfg = {"request_deadline_s": DEADLINE_S, "mango_base": a.mango_base or MANGO_BASE, "openrouter_base": a.or_base or OR_BASE}
    clients = BC.make_clients(cfg, keys)
    w1 = load_w1()
    rng = random.Random(f"exp2_2|{a.vm}|{a.round_id}")
    paths = all_paths()
    if a.models:
        paths = [p for p in paths if p["mk"] in a.models.split(",")]
    scale = a.scale
    # 같은 회차의 G 항목은 모든 경로가 같은 항목을 받음(짝 설계). Mango는 각 항목을 2번
    g_items = rng.sample(w1, max(1, round(ROUND["G"] * scale)))
    plan = defaultdict(list)          # pid -> [(offset_s, kind, item, rep)]
    W = a.window_min * 60
    for p in paths:
        reps = ROUND["mango_reps"] if p["host"] == "mango" else 1
        for rep in range(reps):
            for kind in ("S", "L"):
                for _ in range(max(1, round(ROUND[kind] * scale))):
                    plan[p["pid"]].append((rng.uniform(0, W), kind, None, rep))
            for it in g_items:
                plan[p["pid"]].append((rng.uniform(0, W), "G", it, rep))
        plan[p["pid"]].sort(key=lambda z: z[0])
    gate = MangoGate(a.mango_conc, a.mango_rpm)
    or_gates = {mk: MangoGate(64, a.or_rpm_per_model) for mk in MODELS}   # OR 계정 모델별 한도를 두 VM이 나눔
    writer = Writer(out / "records" / f"round_{a.vm}_{a.round_id}.jsonl")
    run = f"{a.vm}-{a.round_id}"
    t_start = time.time() + 5
    stop = {"budget": False}
    print(f"[{utcnow():%H:%M:%S}] round {run}: 경로 {len(paths)}, 요청 {sum(len(v) for v in plan.values())}, "
          f"창 {a.window_min}분, 누적 추정 ${ledger.total:.2f}/{a.max_usd}", flush=True)

    async def worker(p):
        lim = gate if p["host"] == "mango" else or_gates[p["mk"]]
        meta = {"run": run, "vm": a.vm, "round": a.round_id}
        for i in range(ROUND["warmup"][p["host"]]):            # 예열(분석 제외)
            await one_call(clients, p, "warmup", None, {**meta, "label": "warmup"}, ledger, writer, lim)
        for off, kind, item, rep in plan[p["pid"]]:
            if ledger.total >= a.max_usd:
                stop["budget"] = True
                return
            wait = t_start + 60 + off - time.time()             # 예열 뒤 60초부터 창 시작
            if wait > 0:
                await asyncio.sleep(wait)
            await one_call(clients, p, kind, item, {**meta, "label": "main", "rep": rep,
                                                    "planned_offset_s": round(off, 1)}, ledger, writer, lim)

    await asyncio.gather(*(worker(p) for p in paths))
    print(f"[{utcnow():%H:%M:%S}] round {run} 끝. 누적 추정 ${ledger.total:.2f}"
          f"{' (예산 상한 도달로 중단)' if stop['budget'] else ''}", flush=True)


# ---------------------------------------------------------------- tj (T·J)

async def cmd_tj(a) -> None:
    out = Path(a.out)
    prices = json.loads((out / "prices.json").read_text())
    ledger = Ledger(out, prices)
    keys = BC.load_keys()
    cfg = {"request_deadline_s": DEADLINE_S, "mango_base": a.mango_base or MANGO_BASE, "openrouter_base": a.or_base or OR_BASE}
    clients = BC.make_clients(cfg, keys)
    req_dir = Path(a.requests)
    tasks = {"T": [json.loads(x) for x in (req_dir / "t_tasks.jsonl").open(encoding="utf-8")],
             "J": [json.loads(x) for x in (req_dir / "j_tasks.jsonl").open(encoding="utf-8")]}
    paths = all_paths()
    # 기준점: model_key=tag@T 또는 @J (그 종류만 보냄). 모델당 여러 개 가능
    for x in filter(None, (a.anchors or "").split(",")):
        mk, rest = x.split("=", 1)
        tag, kind = rest.rsplit("@", 1)
        paths.append({"pid": f"{mk}|anchor:{tag}", "mk": mk, "host": "or", "model": MODELS[mk]["or_slug"],
                      "tag": tag, "only": kind, "extra": {"provider": {"order": [tag], "allow_fallbacks": False}}})
    if a.models:
        paths = [p for p in paths if p["mk"] in a.models.split(",")]
    skip = set(filter(None, (a.skip or "").split(",")))       # "pid:T" 형식. prep에서 미지원으로 나온 경로·종류
    # 이미 보낸 건수(실패 포함) → 이어서
    done = Counter()
    for f in (out / "records").glob("tj_*.jsonl"):
        for line in f.open(encoding="utf-8"):
            r = json.loads(line)
            done[(r["pid"], r["kind"], r["seq"])] += 1
    until = datetime.combine(utcnow().date(), datetime.strptime(a.until_utc, "%H:%M").time(), timezone.utc)
    if until <= utcnow():
        sys.exit(f"--until-utc {a.until_utc} 가 이미 지났습니다(오늘 UTC 기준)")
    gate = MangoGate(a.mango_conc, a.mango_rpm)
    or_gates = {mk: MangoGate(64, a.or_rpm_per_model) for mk in MODELS}
    budget_hit = False

    def seqs(p):
        q = TJ["count"][p["mk"]]["mango" if p["host"] == "mango" else "or"]
        lst = []
        for kind in ("T", "J"):
            if f"{p['pid']}:{kind}" in skip or (p.get("only") and p["only"] != kind):
                continue
            n = max(1, round(q[kind] * a.scale))
            lst += [(kind, s) for s in range(n)]
        random.Random(f"exp2_2|tj|{p['pid']}").shuffle(lst)   # 순서 고정(이어 하기 가능)
        return [(k, s) for k, s in lst if not done[(p["pid"], k, s)]]

    if not any(seqs(p) for p in paths):
        print("남은 T·J 요청 없음")
        return
    writer = Writer(out / "records" / f"tj_{utcnow():%Y%m%dT%H%M}.jsonl")
    run = f"tj-{utcnow():%m%d%H%M}"

    async def worker(p):
        nonlocal budget_hit
        for kind, s in seqs(p):
            if utcnow() >= until:
                return
            if ledger.total >= a.max_usd:
                budget_hit = True
                return
            item = tasks[kind][s % len(tasks[kind])]
            await one_call(clients, p, kind, item, {"run": run, "vm": "seoul", "label": "main", "seq": s},
                           ledger, writer, gate if p["host"] == "mango" else or_gates[p["mk"]])

    print(f"[{utcnow():%H:%M:%S}] tj 시작: 경로 {len(paths)}, 남은 요청 {sum(len(seqs(p)) for p in paths)}, "
          f"~{a.until_utc} UTC, 누적 추정 ${ledger.total:.2f}/{a.max_usd}", flush=True)
    await asyncio.gather(*(worker(p) for p in paths))
    print(f"[{utcnow():%H:%M:%S}] tj 멈춤. 누적 추정 ${ledger.total:.2f}{' (예산 상한)' if budget_hit else ''}", flush=True)


# ---------------------------------------------------------------- gens·status

def cmd_gens(a) -> None:
    out = Path(a.out)
    key = BC.load_keys().get("OPENROUTER_API_KEY")
    gp = out / "generations.jsonl"
    have = set()
    if gp.exists():
        have = {json.loads(x)["resp_id"] for x in gp.open(encoding="utf-8")}
    todo = []
    for f in sorted((out / "records").glob("*.jsonl")):
        for line in f.open(encoding="utf-8"):
            r = json.loads(line)
            if r.get("host") == "or" and r.get("resp_id") and r["resp_id"] not in have \
                    and time.time() - (r.get("t_start_utc") or 0) > 150:
                todo.append(r["resp_id"])
    n_ok = 0
    with gp.open("a", encoding="utf-8", newline="\n") as f:
        for rid in todo:
            try:
                d = http_json(f"{OR_BASE}/generation?id={rid}", key)["data"]
                f.write(json.dumps({"resp_id": rid, "data": d}) + "\n")
                n_ok += 1
            except Exception as e:  # noqa: BLE001
                print("generation 실패", rid, str(e)[:80])
            time.sleep(0.05)
    print(f"generation 회수 {n_ok}/{len(todo)}")


def cmd_status(a) -> None:
    out = Path(a.out)
    n, fail, cost = Counter(), Counter(), Counter()
    jud = defaultdict(Counter)
    for f in sorted((out / "records").glob("*.jsonl")):
        for line in f.open(encoding="utf-8"):
            r = json.loads(line)
            k = (f.stem.split("_")[0], r["mk"], r["host"], r["kind"])
            n[k] += 1
            fail[k] += 0 if r.get("ok") else 1
            cost[r["host"]] += r.get("cost_est") or 0
            if r.get("judge"):
                jj = r["judge"]
                jud[(r["pid"], r["kind"])][jj["outcome"] if jj["outcome"] != "judged" else
                                           ("ok" if jj["strict_ok"] else ("lenient_only" if jj["lenient_ok"] else "bad"))] += 1
    for k in sorted(n):
        print(k, "건", n[k], "실패", fail[k])
    for k in sorted(jud):
        print("판정", k, dict(jud[k]))
    print("비용 추정", {k: round(v, 3) for k, v in cost.items()}, "합", round(sum(cost.values()), 3))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prep", "round", "tj", "gens", "status"):
        s = sub.add_parser(name)
        s.add_argument("--out", required=True)
        if name in ("round", "tj"):
            s.add_argument("--max-usd", type=float, required=True, help="이 VM 기록 전체의 추정 비용 상한")
            s.add_argument("--models", help="쉼표로 구분한 모델 키(시험용)")
            s.add_argument("--scale", type=float, default=1.0, help="건수 배율(시험용, 예: 0.1)")
            s.add_argument("--mango-base")
            s.add_argument("--or-base")
        if name == "round":
            s.add_argument("--vm", required=True, choices=["seoul", "iad"])
            s.add_argument("--round-id", required=True)
            s.add_argument("--window-min", type=float, default=35)
            s.add_argument("--mango-conc", type=int, default=1)
            s.add_argument("--mango-rpm", type=int, default=18)
            s.add_argument("--or-rpm-per-model", type=int, default=9, help="두 VM 동시 실행이라 계정 한도 20의 절반 이하")
        if name == "tj":
            s.add_argument("--requests", default="/opt/mango-bench/exp2_2/requests")
            s.add_argument("--until-utc", required=True)
            s.add_argument("--anchors", help="model_key=tag@T 또는 @J를 쉼표로 (T·J 기준점 엔드포인트)")
            s.add_argument("--skip", help="pid:T 또는 pid:J를 쉼표로 (지원 안 되는 경로·종류)")
            s.add_argument("--mango-conc", type=int, default=3)
            s.add_argument("--mango-rpm", type=int, default=36)
            s.add_argument("--or-rpm-per-model", type=int, default=18, help="회차 사이에는 서울 VM만 OR을 씀")
    a = ap.parse_args()
    if a.cmd == "prep":
        cmd_prep(a)
    elif a.cmd == "round":
        asyncio.run(cmd_round(a))
    elif a.cmd == "tj":
        asyncio.run(cmd_tj(a))
    elif a.cmd == "gens":
        cmd_gens(a)
    else:
        cmd_status(a)


if __name__ == "__main__":
    main()
