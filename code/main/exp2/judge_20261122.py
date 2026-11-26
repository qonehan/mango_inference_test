"""실험 2-2회 T(도구 호출)·J(구조화 출력) 형식 판정 (2026-11-22). 설계: 02_분석/30번 3-2-3절.

판정은 두 기준으로 한다. OpenRouter의 정확한 오류 정의가 공개되지 않았기 때문이다.
  strict : T = 모든 호출의 도구 이름이 요청 도구에 있고, 인자가 JSON 객체로 파싱되고, 그 도구의 스키마를 만족
           J = 본문 전체가 JSON으로 파싱되고 스키마를 만족 (코드 울타리 허용 안 함)
  lenient: T = 도구 이름이 있고 인자가 JSON 객체로 파싱됨
           J = 앞뒤 공백·```json 울타리를 벗긴 뒤 JSON 객체로 파싱됨
형식 오류와 따로 세는 결과: no_call(도구 없이 글로 답함), truncated(finish_reason=length), no_finish, http 실패.
오류율 분모는 "응답을 끝까지 받은 요청"(finish_reason이 stop/tool_calls/end_turn 등)이다.
"""
from __future__ import annotations

import json

import jsonschema

DONE = {"stop", "tool_calls", "end_turn", "function_call", "eos"}


def _obj(text: str):
    try:
        v = json.loads(text)
    except (ValueError, TypeError):
        return None
    return v if isinstance(v, dict) else None


def _valid(inst, schema) -> bool:
    try:
        jsonschema.Draft202012Validator(schema).validate(inst)
        return True
    except jsonschema.ValidationError:
        return False


def judge_t(calls: list[dict], content: str, finish: str | None, tools: list[dict]) -> dict:
    """calls: [{"name", "arguments"(전체 문자열)}]."""
    spec = {t["function"]["name"]: t["function"]["parameters"] for t in tools}
    r = {"n_calls": len(calls), "outcome": None, "strict_ok": None, "lenient_ok": None, "bad": []}
    if finish == "length":
        r["outcome"] = "truncated"
        return r
    if finish is None:
        r["outcome"] = "no_finish"
        return r
    if not calls:
        r["outcome"] = "no_call"
        return r
    strict = lenient = True
    for c in calls:
        args = _obj(c.get("arguments") or "{}")
        name_ok = c.get("name") in spec
        if not (name_ok and args is not None):
            lenient = strict = False
            r["bad"].append({"name": c.get("name"), "why": "unknown_tool" if not name_ok else "args_not_json_object"})
            continue
        if not _valid(args, spec[c["name"]]):
            strict = False
            r["bad"].append({"name": c["name"], "why": "schema"})
    r.update(outcome="judged", strict_ok=strict, lenient_ok=lenient)
    return r


def _unfence(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def judge_j(content: str, finish: str | None, schema: dict) -> dict:
    r = {"outcome": None, "strict_ok": None, "lenient_ok": None, "why": None}
    if finish == "length":
        r["outcome"] = "truncated"
        return r
    if finish is None:
        r["outcome"] = "no_finish"
        return r
    strict_obj = _obj((content or "").strip())
    lenient_obj = strict_obj if strict_obj is not None else _obj(_unfence(content))
    strict = strict_obj is not None and _valid(strict_obj, schema)
    r.update(outcome="judged", strict_ok=strict, lenient_ok=lenient_obj is not None,
             why=None if strict else ("not_json" if strict_obj is None else "schema"))
    return r


if __name__ == "__main__":  # 자체 점검
    tools = [{"type": "function", "function": {"name": "f", "parameters": {
        "type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"], "additionalProperties": False}}}]
    assert judge_t([{"name": "f", "arguments": '{"a": 1}'}], "", "tool_calls", tools)["strict_ok"] is True
    assert judge_t([{"name": "f", "arguments": '{"a": "x"}'}], "", "tool_calls", tools)["strict_ok"] is False
    assert judge_t([{"name": "f", "arguments": '{"a": "x"}'}], "", "tool_calls", tools)["lenient_ok"] is True
    assert judge_t([{"name": "g", "arguments": '{}'}], "", "tool_calls", tools)["lenient_ok"] is False
    assert judge_t([{"name": "f", "arguments": '{"a": 1'}], "", "tool_calls", tools)["lenient_ok"] is False
    assert judge_t([], "hello", "stop", tools)["outcome"] == "no_call"
    assert judge_t([], "", "length", tools)["outcome"] == "truncated"
    sch = {"type": "object", "properties": {"x": {"type": "string", "enum": ["a"]}}, "required": ["x"],
           "additionalProperties": False}
    assert judge_j('{"x": "a"}', "stop", sch)["strict_ok"] is True
    assert judge_j('```json\n{"x": "a"}\n```', "stop", sch) == {"outcome": "judged", "strict_ok": False,
                                                                 "lenient_ok": True, "why": "not_json"}
    assert judge_j('{"x": "b"}', "stop", sch)["why"] == "schema"
    print("judge self-test ok")
