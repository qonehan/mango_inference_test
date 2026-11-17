"""질문 세트(W1·W3·W2·L·Z)와 도구 정의를 한 번 생성해 고정 파일로 저장한다.

원천: CPython 태그 v3.12.7의 Lib/(코드)·Doc/(설명문). 토큰 기준: tiktoken o200k_base. 시드 20261116.
같은 원천·시드·규칙이면 같은 파일이 나온다. 모든 파일의 SHA-256을 prompts/manifest.json에 남긴다.
nonce는 여기서 넣지 않는다(실행 시 runner가 붙임).

사용:
  git clone --depth 1 --branch v3.12.7 https://github.com/python/cpython.git /opt/mango-bench/cpython
  python build_prompts.py --cpython /opt/mango-bench/cpython --out prompts
시험용(공식 세트 아님): --tokenizer approx (문자 수/4 근사, manifest에 표시) --allow-no-doc
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

from bench_common import file_sha256

SEED = 20261116

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "Return the contents of a file in the repository.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Repository-relative file path"}},
                       "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "search_code", "description": "Search the repository and return matching lines.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Text or regular expression"}},
                       "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "run_tests", "description": "Run tests and return a summary of results.",
        "parameters": {"type": "object", "properties": {"target": {"type": "string", "description": "Test module or path"}},
                       "required": ["target"], "additionalProperties": False}}},
]


# ---------------------------------------------------------------- 토큰 계산

class Tok:
    def __init__(self, kind: str):
        self.kind = kind
        if kind == "o200k_base":
            import tiktoken
            self.enc = tiktoken.get_encoding("o200k_base")

    def count(self, s: str) -> int:
        return len(self.enc.encode(s, disallowed_special=())) if self.kind != "approx" else (len(s) + 3) // 4

    def head(self, s: str, n: int) -> str:
        if self.kind == "approx":
            return s[: n * 4]
        return self.enc.decode(self.enc.encode(s, disallowed_special=())[:n])


# ---------------------------------------------------------------- 원천

def load_sources(root: Path, allow_no_doc: bool) -> tuple[list, list]:
    lib = root / "Lib"
    code = [p for p in sorted(lib.rglob("*.py")) if not any("test" in x.lower() for x in p.relative_to(lib).parts[:-1])
            and "site-packages" not in p.parts and p.stat().st_size > 2000]
    doc_dir = root / "Doc"
    docs = sorted(doc_dir.rglob("*.rst")) if doc_dir.exists() else []
    if not docs:
        if not allow_no_doc:
            sys.exit("Doc/ 없음: CPython 저장소 체크아웃을 지정하세요 (시험용이면 --allow-no-doc)")
        docs = code  # 시험용: 코드 파일로 대체
    rd = lambda p: (str(p.relative_to(root)).replace("\\", "/"), p.read_text(encoding="utf-8", errors="replace"))
    return [rd(p) for p in code], [rd(p) for p in docs]


def excerpt(rng: random.Random, files: list, tok: Tok, n_tokens: int) -> tuple[str, list[str]]:
    """무작위 파일의 무작위 위치부터 연속 줄을 이어 붙여 정확히 n_tokens 근처로 자른다."""
    parts, used, total = [], [], 0
    while total < n_tokens + 50:
        path, text = files[rng.randrange(len(files))]
        lines = text.splitlines()
        start = rng.randrange(max(1, len(lines) - 40))
        chunk = "\n".join(lines[start:])
        parts.append(f"# File: {path}\n{chunk}")
        used.append(path)
        total += tok.count(parts[-1])
    return tok.head("\n\n".join(parts), n_tokens), used


def fit(rng, files, tok, target: int, tol: int, wrap) -> tuple[str, list]:
    """wrap(excerpt) 전체가 target ± tol 토큰이 되게 발췌 길이를 맞춘다."""
    overhead = tok.count(wrap(""))
    n = target - overhead
    for _ in range(6):
        ex, used = excerpt(random.Random(rng.random()), files, tok, n)
        full = wrap(ex)
        c = tok.count(full)
        if abs(c - target) <= tol:
            return full, used
        n += target - c
    raise RuntimeError(f"길이 맞춤 실패: {c} vs {target}")


# ---------------------------------------------------------------- 작업별 생성

W1_TYPES = {
    "code_write": ("Write a new, self-contained Python function that would be a useful addition to the module "
                   "above. Include a docstring and type hints. Return only the code."),
    "code_explain": "Explain what the code above does, section by section, for an experienced developer.",
    "doc_summary": "Summarize the text above in exactly five bullet points.",
    "json_extract": ('Extract every function, class and constant name that appears above. Respond with JSON only, '
                     'in the form {"functions": [...], "classes": [...], "constants": [...]}.'),
    "math": None,  # 문제를 생성해 붙임
}


def math_problem(rng: random.Random) -> str:
    a, b, c = rng.randint(12, 99), rng.randint(3, 19), rng.randint(100, 999)
    k = rng.randint(2, 9)
    forms = [
        f"A hash table starts with {b} buckets and doubles whenever the load factor exceeds 0.75. "
        f"After inserting {c} keys, how many buckets does it have, and how many resizes happened? Show your work.",
        f"Solve for x: {a}x + {b} = {k}({c} - x). Give the exact value and a decimal to 4 places.",
        f"A loop runs i from 1 to {c} and adds i**2 when i is divisible by {k}. What is the total? Show the formula you use.",
        f"Find all integer pairs (x, y) with 0 < x < y and x*y = {a * k}. Explain how you enumerated them.",
    ]
    return forms[rng.randrange(len(forms))]


def build_w1(rng, code, docs, tok, n=200) -> list:
    items, kinds = [], list(W1_TYPES)
    for i in range(n):
        kind = kinds[i % 5]
        src = docs if kind in ("doc_summary", "json_extract") and i % 2 == 0 else code
        instr = W1_TYPES[kind] or ("Ignore the reference text except as context. Solve this problem step by step:\n"
                                    + math_problem(rng))
        text, used = fit(rng, src, tok, 1000, 50, lambda ex: f"Reference text:\n\n{ex}\n\n---\n{instr}")
        items.append({"id": f"w1-{i:03d}", "kind": kind, "sources": used,
                      "messages": [{"role": "user", "content": text}], "tokens": tok.count(text)})
    return items


W3_INSTR = [
    "A user reports that a function in the code above returns the wrong result for empty input. "
    "Investigate using the available tools. Start by calling the single most useful tool.",
    "Find where the main class above is tested. Use the available tools; call one tool now.",
    "Check whether the behavior described in the code comments matches the tests. Call the appropriate tool first.",
    "Locate other call sites of the first public function above using the tools. Make one tool call.",
]


def build_w3(rng, code, tok, n=200) -> list:
    items = []
    for i in range(n):
        instr = W3_INSTR[i % len(W3_INSTR)]
        text, used = fit(rng, code, tok, 2000, 100, lambda ex: f"Repository excerpt:\n\n{ex}\n\n---\n{instr}")
        items.append({"id": f"w3-{i:03d}", "sources": used, "messages": [{"role": "user", "content": text}],
                      "tokens": tok.count(text)})
    return items


W2_SYSTEM = ("You are a coding agent working in a Python repository. Use the tools to inspect files, search code "
             "and run tests. Always respond to each instruction with exactly one tool call.\n\nRepository context:\n\n")
W2_STEPS = [
    ("Find where the main entry point of this package is defined.", "search_code", "query", "def main"),
    ("Open the file that defines the core class.", "read_file", "path", None),
    ("Run the tests for this module.", "run_tests", "target", None),
    ("Search for uses of the configuration constants.", "search_code", "query", "DEFAULT_"),
    ("Read the helper module that the failing test imports.", "read_file", "path", None),
    ("Search for the error message raised on invalid input.", "search_code", "query", "raise ValueError"),
    ("Open the file with the parsing logic.", "read_file", "path", None),
    ("Run the tests again for the parsing module.", "run_tests", "target", None),
    ("Search for callers of the function you just read.", "search_code", "query", "import "),
    ("Read the file where the fix should be applied.", "read_file", "path", None),
]


def build_w2(rng, code, tok, n_sessions: int, prefix: str) -> list:
    sessions = []
    for s in range(n_sessions):
        ctx, used = fit(rng, code, tok, 16000, 500, lambda ex: W2_SYSTEM + ex)
        turns = []
        for k, (instr, tool, arg, val) in enumerate(W2_STEPS):
            path = used[k % len(used)]
            args = {arg: val if val else (path if arg == "path" else "Lib/test/test_" + Path(path).stem + ".py")}
            result, _ = fit(rng, code, tok, 1500, 100, lambda ex: ex)
            turns.append({"user": instr, "tool_call": {"id": f"call_{s:02d}_{k}", "type": "function",
                                                       "function": {"name": tool, "arguments": json.dumps(args)}},
                          "tool_result": result})
        sess = {"id": f"{prefix}-{s:02d}", "system": ctx, "sources": used, "turns": turns}
        assert prefix_ok(sess), sess["id"]
        sessions.append(sess)
    return sessions


def w2_messages(sess: dict, k: int) -> list[dict]:
    """턴 k(1~10) 입력. 이전 턴은 미리 만든 고정 기록이고 모델의 실제 응답은 넣지 않는다."""
    msgs = [{"role": "system", "content": sess["system"]}]
    for t in sess["turns"][: k - 1]:
        msgs += [{"role": "user", "content": t["user"]},
                 {"role": "assistant", "content": None, "tool_calls": [t["tool_call"]]},
                 {"role": "tool", "tool_call_id": t["tool_call"]["id"], "content": t["tool_result"]}]
    msgs.append({"role": "user", "content": sess["turns"][k - 1]["user"]})
    return msgs


def prefix_ok(sess: dict) -> bool:
    """턴 k 입력이 턴 k−1 입력의 기존 메시지를 바이트 단위로 그대로 포함하는지(마지막 user 뒤에 이어짐) 검사."""
    enc = lambda m: json.dumps(m, ensure_ascii=False, sort_keys=True).encode()
    for k in range(2, len(sess["turns"]) + 1):
        prev, cur = w2_messages(sess, k - 1), w2_messages(sess, k)
        if [enc(m) for m in cur[: len(prev)]] != [enc(m) for m in prev]:
            return False
    return True


def build_l(rng, code, tok, per_len=16) -> list:
    out = []
    instr = "Answer with exactly one word: which programming language is the text above mostly written in?"
    for L in (1000, 10000, 32000):
        for i in range(per_len):
            text, used = fit(rng, code, tok, L, int(L * 0.02), lambda ex: f"{ex}\n\n---\n{instr}")
            out.append({"id": f"l{L // 1000}k-{i:02d}", "length": L, "sources": used,
                        "messages": [{"role": "user", "content": text}], "tokens": tok.count(text)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cpython", required=True)
    ap.add_argument("--out", default="prompts")
    ap.add_argument("--tokenizer", default="o200k_base", choices=["o200k_base", "approx"])
    ap.add_argument("--allow-no-doc", action="store_true")
    a = ap.parse_args()
    root, out = Path(a.cpython), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tok = Tok(a.tokenizer)
    code, docs = load_sources(root, a.allow_no_doc)
    rng = random.Random(SEED)
    sets = {
        "w1.jsonl": build_w1(rng, code, docs, tok),
        "w3.jsonl": build_w3(rng, code, tok),
        "w2_main.jsonl": build_w2(rng, code, tok, 48, "w2"),
        "w2_pilot.jsonl": build_w2(rng, code, tok, 6, "w2p"),
        "l.jsonl": build_l(rng, code, tok),
        "z.jsonl": [{"id": "z-0", "messages": [{"role": "user", "content": "Reply with OK."}]}],
    }
    for name, items in sets.items():
        with open(out / name, "w", encoding="utf-8", newline="\n") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
    (out / "tools.json").write_text(json.dumps(TOOLS, indent=2), encoding="utf-8", newline="\n")
    try:
        tag = subprocess.run(["git", "-C", str(root), "describe", "--tags", "--exact-match"],
                             capture_output=True, text=True).stdout.strip() or None
    except OSError:
        tag = None
    manifest = {"seed": SEED, "tokenizer": a.tokenizer, "official": a.tokenizer == "o200k_base" and tag == "v3.12.7",
                "cpython_tag": tag, "counts": {k: len(v) for k, v in sets.items()},
                "w2_prefix_check": "passed", "sha256": {p.name: file_sha256(p) for p in sorted(out.iterdir())
                                                       if p.name != "manifest.json"}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({k: manifest[k] for k in ("tokenizer", "official", "cpython_tag", "counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
