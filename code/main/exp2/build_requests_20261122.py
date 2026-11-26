"""실험 2-2회 요청 세트 생성 (2026-11-22). 설계: 02_분석/30번 3-2절.

만드는 것 (nonce는 넣지 않음. 실행기가 붙임):
  t_tasks.jsonl  도구 호출 과제 30개. 도구 5~20개(카탈로그 22개에서 고름), 중첩 스키마·enum·배열·필수 인자,
                 병렬 호출 지시 포함, 문맥(CPython 코드 발췌) 1k~8k 토큰.
  j_tasks.jsonl  구조화 출력 과제 30개. 스키마 6종 × 5, response_format json_schema(strict) 호환 스키마
                 (모든 속성 required, additionalProperties false), 문맥 1k~2.5k 토큰.
  manifest.json  시드·토크나이저·개수·SHA-256.
S·L은 실행 시 2-1회 형식으로 생성하고, G는 1회 실험 W1 풀(/opt/mango-bench/prompts/w1.jsonl)을 쓴다.

원천: CPython v3.12.7 Lib/·Doc/ (1회 실험과 같음). 토큰: tiktoken o200k_base. 시드 20261122.
사용(VM): /opt/mango-bench/venv/bin/python build_requests_20261122.py --cpython /opt/mango-bench/cpython \
          --code /opt/mango-bench/code --out /opt/mango-bench/exp2_2/requests
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

SEED = 20261122


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def S(t, **kw):  # 짧은 스키마 도우미
    return {"type": t, **kw}


def OBJ(props: dict, optional: tuple = ()) -> dict:
    return {"type": "object", "properties": props,
            "required": [k for k in props if k not in optional], "additionalProperties": False}


def TOOL(name, desc, props, optional=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": OBJ(props, optional)}}


# ---------------------------------------------------------------- 도구 카탈로그 (22개)
CATALOG = [
    TOOL("read_file", "Return the contents of a repository file, optionally a line range.",
         {"path": S("string"), "start_line": S("integer", minimum=1), "end_line": S("integer", minimum=1)},
         optional=("start_line", "end_line")),
    TOOL("search_code", "Search the repository for a pattern.",
         {"query": S("string"), "file_glob": S("string"), "max_results": S("integer", minimum=1, maximum=100),
          "case_sensitive": S("boolean")}, optional=("file_glob",)),
    TOOL("run_tests", "Run tests and return a summary.",
         {"target": S("string"), "verbosity": S("string", enum=["quiet", "normal", "verbose"]),
          "timeout_s": S("integer", minimum=1, maximum=3600)}, optional=("timeout_s",)),
    TOOL("list_directory", "List files in a directory.", {"path": S("string"), "recursive": S("boolean")}),
    TOOL("write_file", "Write text to a file.",
         {"path": S("string"), "content": S("string"), "mode": S("string", enum=["overwrite", "append"])}),
    TOOL("apply_patch", "Apply exact text replacements to one file.",
         {"path": S("string"),
          "edits": S("array", items=OBJ({"old_text": S("string"), "new_text": S("string")}), minItems=1)}),
    TOOL("git_log", "Show recent commits.",
         {"path": S("string"), "max_count": S("integer", minimum=1, maximum=200),
          "since": S("string", description="ISO date, e.g. 2026-01-31")}, optional=("path", "since")),
    TOOL("git_diff", "Show the diff between two revisions.",
         {"base": S("string"), "head": S("string"), "paths": S("array", items=S("string"))}, optional=("head",)),
    TOOL("create_issue", "Open an issue in the tracker.",
         {"title": S("string"), "body": S("string"),
          "labels": S("array", items=S("string", enum=["bug", "enhancement", "docs", "test", "performance"])),
          "priority": S("string", enum=["low", "medium", "high", "critical"]),
          "assignees": S("array", items=S("string"))}, optional=("assignees",)),
    TOOL("http_request", "Send an HTTP request to an internal service.",
         {"method": S("string", enum=["GET", "POST", "PUT", "DELETE"]), "url": S("string"),
          "headers": S("object", additionalProperties=S("string")), "json_body": S("object")},
         optional=("headers", "json_body")),
    TOOL("query_database", "Run a SQL query against the analytics database.",
         {"sql": S("string"), "params": S("array", items={"type": ["string", "number", "boolean", "null"]}),
          "read_only": S("boolean")}),
    TOOL("schedule_job", "Schedule a recurring job.",
         {"name": S("string"), "cron": S("string", description="5-field cron expression"), "command": S("string"),
          "env": S("object", additionalProperties=S("string"))}, optional=("env",)),
    TOOL("send_notification", "Notify people about something.",
         {"channel": S("string", enum=["email", "slack", "pager"]), "recipients": S("array", items=S("string"), minItems=1),
          "subject": S("string"), "message": S("string"), "urgency": S("string", enum=["low", "normal", "high"])}),
    TOOL("lint_code", "Run the linter.",
         {"paths": S("array", items=S("string"), minItems=1), "rules": S("array", items=S("string")),
          "fix": S("boolean")}, optional=("rules",)),
    TOOL("install_package", "Install a Python package into the dev environment.",
         {"name": S("string"), "version": S("string"), "extras": S("array", items=S("string"))},
         optional=("version", "extras")),
    TOOL("benchmark_function", "Time a function call.",
         {"module": S("string"), "function": S("string"), "args": S("array"),
          "iterations": S("integer", minimum=1, maximum=100000)}),
    TOOL("open_pull_request", "Open a pull request.",
         {"title": S("string"), "description": S("string"), "source_branch": S("string"),
          "target_branch": S("string"), "reviewers": S("array", items=S("string")), "draft": S("boolean")}),
    TOOL("get_ci_status", "Get the status of a CI pipeline.",
         {"pipeline_id": S("integer", minimum=1), "include_logs": S("boolean")}),
    TOOL("profile_memory", "Profile memory use of a command.",
         {"command": S("string"), "duration_s": S("number", minimum=0.1, maximum=600)}),
    TOOL("summarize_logs", "Summarize application logs.",
         {"log_path": S("string"), "level": S("string", enum=["DEBUG", "INFO", "WARNING", "ERROR"]),
          "last_n": S("integer", minimum=1, maximum=10000)}),
    TOOL("rename_symbol", "Rename a symbol across the code base.",
         {"old_name": S("string"), "new_name": S("string"), "scope": S("string", enum=["file", "module", "project"]),
          "path": S("string")}),
    TOOL("run_shell", "Run a shell command in the repository.",
         {"command": S("string"), "cwd": S("string"), "timeout_s": S("integer", minimum=1, maximum=600)},
         optional=("cwd",)),
]
BY_NAME = {t["function"]["name"]: t for t in CATALOG}

# 지시 10종 × 3 = 30. {f1}{f2}=발췌에 쓰인 파일, {sym}=발췌의 첫 def/class 이름. need = 반드시 포함할 도구.
T_INSTR = [
    ("Open an issue about a possible bug in `{sym}` from the code above: label it bug and test, priority high, "
     "assign it to alice and bob. Write a two-sentence body.", ["create_issue"], 1),
    ("In parallel, read `{f1}` lines 1-80 and `{f2}` lines 1-80, and search the repository for `{sym}` "
     "(case-sensitive, at most 20 results). Make all three tool calls now.", ["read_file", "search_code"], 3),
    ("Replace the first occurrence of `def {sym}` in `{f1}` with `def {sym}_v2` and also add the comment "
     "`# renamed for v2` right above it, using a single patch with two edits.", ["apply_patch"], 1),
    ("Schedule a nightly job named `lint-{sym}` at 02:30 every day that runs `python -m pylint {f1}` with "
     "environment variables PYTHONWARNINGS=ignore and CI=1.", ["schedule_job"], 1),
    ("Run the tests for `{f1}` verbosely with a 900 second timeout, and at the same time get the status of CI "
     "pipeline 4821 including logs.", ["run_tests", "get_ci_status"], 2),
    ("Query the analytics database for the 10 most frequent error codes since 2026-11-01 using a parameterized, "
     "read-only SQL statement with the date as a parameter.", ["query_database"], 1),
    ("Send a high-urgency Slack notification to #core-dev and @carol saying that `{sym}` in `{f1}` needs review "
     "before the release. Include a short subject.", ["send_notification"], 1),
    ("Call the internal endpoint POST https://ci.internal/api/v1/reruns with header X-Team: core and JSON body "
     '{{"module": "{f1}", "reason": "flaky", "retries": 2}}.', ["http_request"], 1),
    ("Open a draft pull request from branch `fix/{sym}` into `main` titled after the fix to `{sym}`, with a "
     "three-line description and reviewers dave and erin; also lint `{f1}` and `{f2}` with auto-fix enabled.",
     ["open_pull_request", "lint_code"], 2),
    ("Rename `{sym}` to `{sym}_impl` across the whole project starting from `{f1}`, then show the git diff "
     "against `main` limited to `{f1}`.", ["rename_symbol", "git_diff"], 2),
]
T_CTX = [1000, 2000, 3000, 4000, 8000]   # 도구 정의·지시 포함 전체 약 2k~10k (평균 약 5k, 예산 가정에 맞춤)
T_SYSTEM = ("You are an autonomous software engineering agent operating on a Python repository through tools. "
            "Use the provided tools to carry out the user's request. Respond with tool calls only; do not write "
            "prose. When the request asks for several independent actions, issue the tool calls in parallel.")

# ---------------------------------------------------------------- 구조화 출력 스키마 6종
J_SCHEMAS = {
    "api_inventory": OBJ({
        "module_path": S("string"),
        "functions": S("array", items=OBJ({"name": S("string"),
                                          "parameters": S("array", items=OBJ({"name": S("string"), "has_default": S("boolean")})),
                                          "is_private": S("boolean")})),
        "has_classes": S("boolean")}),
    "code_review": OBJ({
        "summary": S("string"),
        "issues": S("array", items=OBJ({"severity": S("string", enum=["info", "minor", "major", "critical"]),
                                       "category": S("string", enum=["correctness", "performance", "style", "security", "docs"]),
                                       "symbol": S("string"), "description": S("string")})),
        "overall_quality": S("string", enum=["poor", "fair", "good", "excellent"])}),
    "doc_digest": OBJ({
        "title": S("string"), "key_points": S("array", items=S("string")),
        "audience": S("string", enum=["beginner", "intermediate", "expert"]),
        "contains_code_examples": S("boolean")}),
    "import_map": OBJ({
        "imports": S("array", items=OBJ({"module": S("string"), "names": S("array", items=S("string")),
                                        "is_stdlib": S("boolean")})),
        "total_modules": S("integer")}),
    "test_plan": OBJ({
        "target": S("string"),
        "cases": S("array", items=OBJ({"name": S("string"), "input": S("string"), "expected": S("string"),
                                      "kind": S("string", enum=["unit", "edge", "error", "integration"])})),
        "needs_mocking": S("boolean")}),
    "change_log": OBJ({
        "version_bump": S("string", enum=["major", "minor", "patch"]),
        "entries": S("array", items=OBJ({"type": S("string", enum=["added", "changed", "fixed", "removed"]),
                                        "symbol": S("string"), "note": S("string")})),
        "breaking": S("boolean"), "reviewers_needed": S("integer")}),
}
J_INSTR = {
    "api_inventory": "List the functions defined in the code above with their parameters.",
    "code_review": "Review the code above and report concrete issues.",
    "doc_digest": "Digest the text above for a reader deciding whether to read it in full.",
    "import_map": "Map every import statement in the code above.",
    "test_plan": "Write a test plan for the most important function in the code above (4 to 8 cases).",
    "change_log": "Imagine a maintainer just refactored the code above; draft the change log entries (3 to 6).",
}
J_SYSTEM = "Return a single JSON object that conforms exactly to the provided JSON schema. No prose, no markdown."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cpython", required=True)
    ap.add_argument("--code", required=True, help="1회 실험 code 폴더(build_prompts.py의 원천·토큰 함수 재사용)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--tokenizer", default="o200k_base", choices=["o200k_base", "approx"])
    a = ap.parse_args()
    sys.path.insert(0, a.code)
    import build_prompts as BP  # noqa: E402  1회 실험과 같은 원천·발췌 함수

    tok = BP.Tok(a.tokenizer)
    code, docs = BP.load_sources(Path(a.cpython), allow_no_doc=a.tokenizer == "approx")
    rng = random.Random(SEED)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    t_items = []
    for i in range(30):
        instr, need, n_calls = T_INSTR[i % 10]
        ctx = T_CTX[i % 5]
        n_tools = 5 + (i * 7) % 16                      # 5~20
        others = [t for t in CATALOG if t["function"]["name"] not in need]
        tools = [BY_NAME[n] for n in need] + random.Random(rng.random()).sample(others, n_tools - len(need))
        random.Random(rng.random()).shuffle(tools)
        ex, used = BP.excerpt(random.Random(rng.random()), code, tok, ctx)
        sym = next((ln.split()[1].split("(")[0].rstrip(":") for ln in ex.splitlines()
                    if ln.startswith(("def ", "class ")) and len(ln.split()) > 1), "main")
        f1, f2 = used[0], used[1 if len(used) > 1 else 0]
        msg = instr.format(f1=f1, f2=f2, sym=sym)
        t_items.append({"id": f"t-{i:02d}", "instr_type": i % 10, "need_tools": need, "expected_calls": n_calls,
                        "n_tools": len(tools), "ctx_tokens": ctx, "sources": used, "tools": tools,
                        "messages": [{"role": "system", "content": T_SYSTEM},
                                     {"role": "user", "content": f"Repository excerpt:\n\n{ex}\n\n---\nRequest: {msg}"}]})
        t_items[-1]["tokens"] = tok.count(json.dumps(t_items[-1]["messages"])) + tok.count(json.dumps(tools))

    j_items = []
    names = list(J_SCHEMAS)
    for i in range(30):
        kind = names[i % 6]
        n = [1000, 1500, 2000, 2500, 1200][i // 6]
        src = docs if kind == "doc_digest" else code
        ex, used = BP.excerpt(random.Random(rng.random()), src, tok, n)
        j_items.append({"id": f"j-{i:02d}", "schema_name": kind, "ctx_tokens": n, "sources": used,
                        "schema": J_SCHEMAS[kind],
                        "messages": [{"role": "system", "content": J_SYSTEM},
                                     {"role": "user", "content": f"Source:\n\n{ex}\n\n---\nTask: {J_INSTR[kind]}"}]})
        j_items[-1]["tokens"] = tok.count(json.dumps(j_items[-1]["messages"])) + tok.count(json.dumps(J_SCHEMAS[kind]))

    for name, items in (("t_tasks.jsonl", t_items), ("j_tasks.jsonl", j_items)):
        with open(out / name, "w", encoding="utf-8", newline="\n") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
    man = {"seed": SEED, "tokenizer": a.tokenizer, "counts": {"t": len(t_items), "j": len(j_items)},
           "t_tokens": [it["tokens"] for it in t_items], "j_tokens": [it["tokens"] for it in j_items],
           "sha256": {p.name: sha(p) for p in sorted(out.glob("*.jsonl"))},
           "script_sha256": sha(Path(__file__))}
    (out / "manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({k: man[k] for k in ("counts", "t_tokens", "j_tokens")}))


if __name__ == "__main__":
    main()
