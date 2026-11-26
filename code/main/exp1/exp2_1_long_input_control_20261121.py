#!/usr/bin/env python3
"""실험 2-1회 긴 입력 대조군: 요청 모양(입력 길이)에 따라 기본 라우팅 배분이 달라지는지 확인.

본 실험(exp2_1_routing_20261120.py)과 같은 시간 창(매시 :05~:15)에 같은 방식으로 호출하되,
system 메시지에 약 3,000토큰 분량의 채움 문장을 넣는다. 다른 조건(도구 없음, max_tokens 16,
추론 설정 기본값, 제공사 지정 없음)은 본 실험과 같다.

- 고착 회피와 캐시 회피: 요청마다 고유 번호를 system 메시지 **맨 앞**에 둔다. 공급사의 앞부분 일치
  캐시가 맞지 않고, OpenRouter의 대화 해시도 요청마다 달라진다.
- 채움 문장은 요청 번호로 시드를 준 의사 난수 단어열이라 요청마다 내용이 다르다.
- 공개 지표는 본 실험 프로세스가 수집하므로 여기서는 호출과 generation 회수만 한다.

사용 (VM, 본 실험 코드와 같은 폴더):
  OPENROUTER_API_KEY=... python3 exp2_1_long_input_control_20261121.py --test --out <폴더>
  OPENROUTER_API_KEY=... python3 exp2_1_long_input_control_20261121.py --hours 6 --start-utc 2026-11-21T07:00 --max-usd 0.8 --out <폴더>
"""
import argparse
import json
import os
import random
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exp2_1_routing_20261120 as base  # noqa: E402

KEEP = ("deepseek-v4.1-flash-1017", "glm-5.3", "glm-5.3-flash")   # 기본값. --models 로 Kimi 추가 가능
WORDS = ("river market signal engine harbor ledger window planet garden circuit lantern meadow "
         "copper thunder orbit canvas pillar velvet summit quartz anchor bridge castle desert "
         "forest glacier island jungle kettle ladder marble needle ocean pepper quiver ribbon "
         "saddle temple umbrella valley wagon yonder zephyr amber basin cedar dune ember fable "
         "grove hollow ivory jasper kernel lagoon mosaic nectar oasis prism quill rapids sierra "
         "tundra utopia vortex willow yarrow zenith").split()
FILLER_WORDS = 2300   # 약 3,000토큰 (모델 토크나이저에 따라 다름)


def filler(seed):
    rnd = random.Random(seed)
    out = []
    for i in range(FILLER_WORDS):
        out.append(rnd.choice(WORDS))
        if i % 14 == 13:
            out[-1] += "."
    return " ".join(out)


class LongRouter(base.Router):
    def body(self, m, seq, fixed_prompt=False):
        tag = f"{self.run_id}-{m['key']}-{seq:06d}"
        b = {"model": m["api_id"],
             "messages": [{"role": "system",
                           "content": f"Request {tag}. Background notes follow; ignore them.\n\n"
                                      f"{filler(tag)}\n\nAnswer in one short word."},
                          {"role": "user", "content": f"[{tag}] Reply with the single word OK."}],
             "max_tokens": self.cfg["max_tokens"], "stream": False}
        return b


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--start-utc")
    ap.add_argument("--max-usd", type=float, default=0.8)
    ap.add_argument("--calls-per-hour", type=int, default=100)
    ap.add_argument("--test", action="store_true", help="모델별 1회만 보내고 토큰·비용 확인")
    ap.add_argument("--models", default=",".join(KEEP), help="쉼표로 구분한 모델 키 (기본: Kimi 제외 3개)")
    a = ap.parse_args()

    keep = set(a.models.split(","))
    base.MODELS[:] = [m for m in base.MODELS if m["key"] in keep]
    cfg = dict(base.DEFAULTS, max_usd=a.max_usd, calls_per_model_per_hour=a.calls_per_hour)
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY 환경변수가 필요합니다.")
    store = base.Store(a.out)
    (Path(a.out) / "config.json").write_text(json.dumps(
        {"argv": sys.argv[1:], "cfg": cfg, "models": base.MODELS, "filler_words": FILLER_WORDS,
         "written_utc": base.utcnow().isoformat()}, ensure_ascii=False, indent=2), encoding="utf-8")

    if a.test:
        r = LongRouter(store, cfg, run_id="lt" + base.utcnow().strftime("%H%M%S"), key=key)
        for m in base.MODELS:
            r.call(m, 1, time.time(), label="long-test")
        end = time.time()
        threading.Thread(target=r.generation_worker, args=(end - 900,), daemon=True).start()
        while r.gen_heap and time.time() < end + 400:
            time.sleep(3)
        store.event(f"long-test 끝: 비용 ${r.spent:.5f}")
        base.summarize(a.out)
        return

    start = (datetime.fromisoformat(a.start_utc).replace(tzinfo=timezone.utc).timestamp()
             if a.start_utc else (time.time() // 3600 + 1) * 3600)
    end = start + a.hours * 3600
    store.event(f"long 시작 예정 {base.iso(start)} ~ {base.iso(end)}, 설정 {json.dumps(cfg, ensure_ascii=False)}")
    rt = LongRouter(store, cfg, run_id=datetime.fromtimestamp(start, timezone.utc).strftime("L%Y%m%d%H"), key=key)
    rt.run(start, end)
    store.manifest()
    store.event(f"끝: 누적 비용 ${rt.spent:.4f}")


if __name__ == "__main__":
    main()
