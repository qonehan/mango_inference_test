"""품질 점검 Q (프로토콜 2-2·5-1·7절): 문항 생성(build) → 실행(run) → 채점(grade).

문항: MATH 상위 난이도 200(Level 5), MMLU-Pro 150, BFCL 50(simple). 시드 20261116으로 추출.
데이터는 저장소에 넣지 않는다(MATH는 DMCA 이력이 있어 문항 원문 재배포 금지). 실행 장소에서 받은
로컬 JSONL을 읽고, 문항 ID·해시만 기록한다. 입력 형식(한 줄에 한 문항):
  MATH:     {"problem", "solution", "level", "type"}            (EleutherAI/hendrycks_math 내보내기)
  MMLU-Pro: {"question_id", "question", "options": [...], "answer": "A".., "category"}
  BFCL:     {"id", "question": [[{"role","content"}]], "function": [...]} + 정답 파일 {"id", "ground_truth": [...]}
경로: M·OR-기본·OR-고정 5곳 = 7경로 × 4모델. 미응답·잘림·채점 불가 출력은 오답.

사용:
  python q_bench.py build --math math.jsonl --mmlu mmlu_pro.jsonl --bfcl bfcl_simple.jsonl --bfcl-answers ans.jsonl --out q
  python q_bench.py run --config config/day1.yaml --q q --out runs/Q [--limit 20]   # 파일럿은 --limit 20
  python q_bench.py grade runs/Q
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

SEED = 20261116


def jl(p) -> list:
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------- build

def build(a) -> None:
    rng = random.Random(SEED)
    items = []
    math_rows = [r for r in jl(a.math) if str(r.get("level")).strip() == "Level 5"]
    for i, r in enumerate(rng.sample(math_rows, 200)):
        items.append({"id": f"math-{hashlib.sha256(r['problem'].encode()).hexdigest()[:12]}", "set": "MATH",
                      "messages": [{"role": "user", "content": r["problem"] +
                                    "\n\nPut your final answer within \\boxed{}."}],
                      "answer": boxed(r["solution"])})
    mm = jl(a.mmlu)
    for r in rng.sample(mm, 150):
        letters = "ABCDEFGHIJ"[: len(r["options"])]
        opts = "\n".join(f"{l}. {o}" for l, o in zip(letters, r["options"]))
        items.append({"id": f"mmlu-{r['question_id']}", "set": "MMLU-Pro",
                      "messages": [{"role": "user", "content": f"{r['question']}\n\n{opts}\n\n"
                                    "Answer with the letter of the correct option, in the form 'Answer: X'."}],
                      "answer": r["answer"]})
    ans = {r["id"]: r["ground_truth"] for r in jl(a.bfcl_answers)}
    bf = [r for r in jl(a.bfcl) if r["id"] in ans]
    for r in rng.sample(bf, 50):
        tools = [{"type": "function", "function": {"name": f["name"].replace(".", "_"),
                  "description": f.get("description", ""), "parameters": fix_schema(f["parameters"])}}
                 for f in r["function"]]
        items.append({"id": f"bfcl-{r['id']}", "set": "BFCL", "messages": r["question"][0], "tools": tools,
                      "answer": ans[r["id"]]})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "q_items.jsonl"
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    meta = {"seed": SEED, "counts": {s: sum(i["set"] == s for i in items) for s in ("MATH", "MMLU-Pro", "BFCL")},
            "ids": [i["id"] for i in items], "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "sources": {"MATH": a.math, "MMLU-Pro": a.mmlu, "BFCL": a.bfcl},
            "note": "q_items.jsonl은 공개 저장소에 올리지 않는다(문항 원문). ID·해시만 공개"}
    (out / "q_manifest.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(meta["counts"], meta["sha256"][:16])


def fix_schema(s: dict) -> dict:
    """BFCL의 'dict'·'float' 등 비표준 타입을 JSON Schema로 바꾼다."""
    if isinstance(s, dict):
        s = dict(s)
        t = s.get("type")
        s["type"] = {"dict": "object", "float": "number", "tuple": "array", "any": "string"}.get(t, t)
        for k in ("properties",):
            if k in s:
                s[k] = {n: fix_schema(v) for n, v in s[k].items()}
        if "items" in s:
            s["items"] = fix_schema(s["items"])
    return s


# ---------------------------------------------------------------- grade helpers

def boxed(text: str | None) -> str | None:
    if not text:
        return None
    i = text.rfind("\\boxed")
    if i < 0:
        return None
    j = text.find("{", i)
    depth, k = 0, j
    while k < len(text):
        depth += {"{": 1, "}": -1}.get(text[k], 0)
        if depth == 0:
            return text[j + 1:k]
        k += 1
    return None


def norm_math(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.replace(" ", "").replace("\\!", "").replace("\\left", "").replace("\\right", "")
    s = s.replace("dfrac", "frac").replace("tfrac", "frac").replace("^\\circ", "").replace("^{\\circ}", "")
    s = re.sub(r"\\text\{.*?\}", "", s).replace("$", "").rstrip(".")
    return s


def grade_one(item: dict, rec: dict) -> bool:
    if not rec.get("ok"):
        return False
    if item["set"] == "MATH":
        return norm_math(boxed(rec.get("text_full") or rec.get("text"))) == norm_math(item["answer"])
    if item["set"] == "MMLU-Pro":
        m = re.findall(r"Answer:\s*\(?([A-J])\)?", rec.get("text_full") or rec.get("text") or "")
        return bool(m) and m[-1] == item["answer"]
    # BFCL simple: 함수 이름과 인자 값이 허용 답 중 하나와 일치
    try:
        args = json.loads(rec.get("tool_args") or "{}")
    except json.JSONDecodeError:
        return False
    for gt in item["answer"]:
        for fname, allowed in gt.items():
            if fname.replace(".", "_") != rec.get("tool_name"):
                continue
            ok = True
            for k, vals in allowed.items():
                if k not in args:
                    ok = ok and "" in vals  # 선택 인자
                elif args[k] not in vals:
                    ok = False
            if ok:
                return True
    return False


# ---------------------------------------------------------------- run / grade

async def run(a) -> None:
    from bench_common import Limits, arms_for, load_config, load_keys, make_clients, nonce_hex, stream_call, with_nonce
    cfg = load_config(a.config)
    items = jl(Path(a.q) / "q_items.jsonl")
    if a.limit:
        per = {"MATH": a.limit // 2, "MMLU-Pro": a.limit * 3 // 8, "BFCL": a.limit - a.limit // 2 - a.limit * 3 // 8}
        items = [i for s in per for i in [x for x in items if x["set"] == s][: per[s]]]
    keys = load_keys()
    clients = make_clients(cfg, keys, a.mango_base, a.or_base)
    lim = Limits(cfg)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    f = open(out / "q_raw.jsonl", "a", encoding="utf-8")

    async def one(mk, arm, it):
        m = cfg["models"][mk]
        async with lim.ctx(arm["host"], mk):
            rec = await stream_call(clients[arm["host"]], host=arm["host"], model=arm["model"],
                                    messages=with_nonce(it["messages"], nonce_hex()), max_tokens=cfg["max_tokens_cap"],
                                    temperature=m["temperature"], top_p=m["top_p"], extra=arm["extra"],
                                    tools=it.get("tools"), need_tool=bool(it.get("tools")),
                                    deadline_s=cfg["request_deadline_s"], keep_text=100000,
                                    meta={"task": "Q", "model_key": mk, "arm": arm["arm"], "item_id": it["id"],
                                          "set": it["set"]})
        rec["text_full"], rec["text"] = rec.pop("text"), None
        rec.pop("chunks", None)
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()

    async def lane(mk, arm):
        for it in items:
            await one(mk, arm, it)

    lanes = []
    for mk in cfg["models"]:
        arms = arms_for(cfg, mk)
        for name in ["M", "OR-default"] + [f"OR-pin:{p}" for p in cfg["models"][mk]["pins"]]:
            lanes.append(lane(mk, arms[name]))
    await asyncio.gather(*lanes)
    print("완료:", out / "q_raw.jsonl")


def grade(a) -> None:
    d = Path(a.folder)
    items = {i["id"]: i for i in jl(Path(a.q) / "q_items.jsonl")}
    recs = jl(d / "q_raw.jsonl")
    table = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    rows = []
    for r in recs:
        it = items[r["item_id"]]
        c = grade_one(it, r)
        table[(r["model_key"], r["arm"])][it["set"]][0] += c
        table[(r["model_key"], r["arm"])][it["set"]][1] += 1
        rows.append({"model_key": r["model_key"], "arm": r["arm"], "item_id": r["item_id"], "set": it["set"],
                     "correct": int(c), "ok": r.get("ok"), "error": r.get("error")})
    with open(d / "q_graded.jsonl", "w", encoding="utf-8") as f:
        for x in rows:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    for (mk, arm), sets in sorted(table.items()):
        print(mk, arm, {s: f"{c}/{n}" for s, (c, n) in sets.items()})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    for k in ("--math", "--mmlu", "--bfcl", "--bfcl-answers", "--out"):
        b.add_argument(k, required=True)
    r = sp.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--q", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--mango-base", default=None)
    r.add_argument("--or-base", default=None)
    g = sp.add_parser("grade")
    g.add_argument("folder")
    g.add_argument("--q", required=True)
    a = ap.parse_args()
    if a.cmd == "build":
        build(a)
    elif a.cmd == "run":
        asyncio.run(run(a))
    else:
        grade(a)
