"""측정 코드 단위 시험 (네트워크·키 없음). python -m unittest discover -s code -p "test_*.py" """
import json
import unittest

from analyze import geo_ratio, strat_boot, verdict
from bench_common import RateLimiter, classify_error, validate_tool, with_nonce
from build_prompts import prefix_ok, w2_messages
from q_bench import boxed, fix_schema, grade_one, norm_math


class Bench(unittest.TestCase):
    def test_nonce_first_system(self):
        m = with_nonce([{"role": "user", "content": "q"}], "ab")
        self.assertEqual(m[0], {"role": "system", "content": "ID: ab"})
        m = with_nonce([{"role": "system", "content": "S"}, {"role": "user", "content": "q"}], "ab")
        self.assertTrue(m[0]["content"].startswith("ID: ab\n\nS"))
        self.assertEqual(len(m), 2)

    def test_w2_prefix(self):
        turns = [{"user": f"u{k}", "tool_call": {"id": f"c{k}", "type": "function",
                  "function": {"name": "read_file", "arguments": "{}"}}, "tool_result": f"r{k}"} for k in range(10)]
        s = {"system": "S", "turns": turns}
        self.assertTrue(prefix_ok(s))
        self.assertEqual(len(w2_messages(s, 3)), 1 + 3 * 2 + 1)

    def test_error_classes(self):
        class E(Exception):
            def __init__(self, status, body):
                self.status_code, self.body = status, body
        self.assertEqual(classify_error(E(429, "new-account-rpm limit"), "or"), ("ours", "or_account_rate_limit"))
        self.assertEqual(classify_error(E(429, "Provider returned error"), "or"), ("endpoint", "provider_429"))
        self.assertEqual(classify_error(E(429, "x"), "mango"), ("ours", "mango_rate_limit"))
        self.assertEqual(classify_error(E(503, "x"), "or")[0], "endpoint")
        self.assertEqual(classify_error(TimeoutError(), "or"), ("endpoint", "deadline"))

    def test_verdict(self):
        self.assertEqual(verdict((0.80, 0.88), True), "Mango 우위")
        self.assertEqual(verdict((0.95, 1.05), True), "동등")
        self.assertEqual(verdict((1.12, 1.3), False), "Mango 우위")
        self.assertEqual(verdict((0.85, 1.05), True), "판정 불가")

    def test_boot(self):
        strata = {"1": [(2.0, 1.0)] * 10, "2": [(2.0, 1.0)] * 10}
        lo, hi = strat_boot(strata, geo_ratio, 0.05, reps=200)
        self.assertAlmostEqual(lo, 2.0)
        self.assertAlmostEqual(hi, 2.0)

    def test_math_grade(self):
        self.assertEqual(boxed("so \\boxed{\\frac{1}{2}}."), "\\frac{1}{2}")
        self.assertEqual(norm_math("\\dfrac{1}{2}"), norm_math("\\frac{1}{2}"))
        item = {"set": "MATH", "answer": "\\frac{1}{2}"}
        self.assertTrue(grade_one(item, {"ok": True, "text_full": "x = \\boxed{\\dfrac{1}{2}}"}))
        self.assertFalse(grade_one(item, {"ok": False, "text_full": "\\boxed{\\frac{1}{2}}"}))

    def test_mmlu_bfcl_grade(self):
        self.assertTrue(grade_one({"set": "MMLU-Pro", "answer": "C"}, {"ok": True, "text_full": "... Answer: C"}))
        item = {"set": "BFCL", "answer": [{"math.area": {"r": [2, 2.0], "unit": ["", "cm"]}}]}
        self.assertTrue(grade_one(item, {"ok": True, "tool_name": "math_area", "tool_args": json.dumps({"r": 2})}))
        self.assertFalse(grade_one(item, {"ok": True, "tool_name": "math_area", "tool_args": json.dumps({"r": 3})}))
        self.assertEqual(fix_schema({"type": "dict", "properties": {"x": {"type": "float"}}})["properties"]["x"]["type"],
                         "number")

    def test_tool_validation(self):
        tools = [{"type": "function", "function": {"name": "search_code", "parameters": {
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
            "additionalProperties": False}}}]
        self.assertTrue(validate_tool('{"query": "x"}', "search_code", tools))
        self.assertFalse(validate_tool('{"query": "x"}{"query": "y"}', "search_code", tools))  # 이어 붙인 인자는 무효
        self.assertFalse(validate_tool('{"path": "x"}', "search_code", tools))
        self.assertFalse(validate_tool('{"query": "x"}', "read_file", tools))

    def test_rate_limiter_counts(self):
        import asyncio
        async def go():
            rl = RateLimiter(5)
            for _ in range(5):
                await rl.acquire()
            return len(rl.times)
        self.assertEqual(asyncio.run(go()), 5)


if __name__ == "__main__":
    unittest.main()


class TestExpectLength(unittest.TestCase):
    """v1.0: L·Z·준비 호출의 의도된 length 종료는 성공으로 기록한다."""

    def test_length_expected_is_ok(self):
        import asyncio
        from types import SimpleNamespace
        import bench_common as bc

        def chunk(delta, finish=None, usage=None):
            d = SimpleNamespace(model_dump=lambda: delta)
            ch = [] if usage else [SimpleNamespace(delta=d, finish_reason=finish)]
            return SimpleNamespace(id="x", system_fingerprint=None, choices=ch,
                                   usage=SimpleNamespace(model_dump=lambda: usage) if usage else None)

        class Stream:
            def __init__(self):
                self.items = [chunk({"content": "a", "reasoning": None, "reasoning_content": None, "tool_calls": None}, "length"),
                              chunk({}, usage={"prompt_tokens": 5, "completion_tokens": 1})]

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.items:
                    raise StopAsyncIteration
                return self.items.pop(0)

        class Raw:
            headers = {}

            def parse(self):
                return Stream()

        class Client:
            class chat:
                class completions:
                    class with_raw_response:
                        @staticmethod
                        async def create(**kw):
                            return Raw()

        for expect, ok in ((True, True), (False, False)):
            rec = asyncio.run(bc.stream_call(Client, host="mango", model="m", messages=[], max_tokens=1,
                                             temperature=1.0, top_p=1.0, expect_length=expect))
            self.assertEqual(rec["ok"], ok, rec.get("error"))
            if expect:
                self.assertEqual(rec.get("status_note"), "length_expected")
                self.assertIsNone(rec["decode_tps"])
