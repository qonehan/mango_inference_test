#!/usr/bin/env python3
"""실험 2-1회: 기본 라우팅 선택과 실시간 지표 동시 수집 (설계: 02_분석/28_실험설계_2-1회_기본라우팅과_실시간지표_20261120.md).

Python 표준 라이브러리만 사용한다. 명령:

  collect  공개 지표만 수집 (API 키 불필요). --once 는 모든 작업을 한 번씩만 실행(점검용).
  route    기본 라우팅 호출 + generation 회수만 실행 (OPENROUTER_API_KEY 필요). --dry-run 은 계획만 출력.
  run      collect + route 를 함께 실행 (본 실험).
  pretest  사전 시험: 모델별 고유 프롬프트 N회 + 같은 프롬프트 반복 N회(고착 확인), 비용 요약.
  summarize  저장된 기록에서 시간별 선택 분포·오류·비용·수집 완결성 요약.

예 (저장소 루트에서):
  python 03_MangoBoost/08_도구/exp2_1_routing_20261120.py collect --once --out /tmp/exp_check
  OPENROUTER_API_KEY=... python 03_MangoBoost/08_도구/exp2_1_routing_20261120.py pretest --out <폴더>
  OPENROUTER_API_KEY=... python 03_MangoBoost/08_도구/exp2_1_routing_20261120.py run --hours 24 --start-utc 2026-11-21T00:00 --out <폴더>

출력 폴더 구조:
  public/YYYYMMDDTHH.jsonl.gz   공개 API 응답 원본 (URL·시각·상태·헤더·본문)
  routing/YYYYMMDDTHH.jsonl.gz  기본 라우팅 호출 원본 (요청 본문·응답 본문·헤더, 키 제외)
  generation/YYYYMMDDTHH.jsonl.gz  generation 기록 원본 (provider_responses 포함)
  events.log                    경고·중단·예산 기록
  manifest_YYYYMMDD.json        날짜별 건수·실패·파일 SHA-256
"""
import argparse
import gzip
import hashlib
import heapq
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://openrouter.ai"
UA = "mango-bench/1.0"

# 모델: permaslug(통계 API) / API 모델 id(호출) / 공식 endpoints 경로
MODELS = [
    {"key": "deepseek-v4.1-flash-1017", "permaslug": "deepseek/deepseek-v4.1-flash-20261017",
     "api_id": "deepseek/deepseek-v4.1-flash-1017"},
    {"key": "glm-5.3", "permaslug": "z-ai/glm-5.3-20260816", "api_id": "z-ai/glm-5.3"},
    {"key": "glm-5.3-flash", "permaslug": "z-ai/glm-5.3-flash-20260826", "api_id": "z-ai/glm-5.3-flash"},
    {"key": "kimi-k3.1-pro", "permaslug": "moonshotai/kimi-k3.1-pro-20261023", "api_id": "moonshotai/kimi-k3.1-pro"},
]

DEFAULTS = {
    "calls_per_model_per_hour": 200,   # 28번 6절
    "window_start_min": 5,             # 매시 :05 ~ :15
    "window_minutes": 10,
    "max_tokens": 16,                  # 28번 2-1: 고정 (사전 시험 후 확정)
    "reasoning": None,                 # None = 모델 기본값 (파라미터를 보내지 않음)
    "public_pause_s": 0.5,
    "generation_delay_s": 30,
    "max_usd": 10.0,                   # generation total_cost 합계 상한 (넘으면 호출 중단)
    "top_uptime_hourly_per_model": 10,
    "hourly_offset_min": 20,           # 정시 작업은 :20 (호출 창과 겹치지 않게)
    "daily_utc_hhmm": "03:10",
}

STOP = threading.Event()
_lock = threading.Lock()


# ---------------------------------------------------------------- 공통
def utcnow():
    return datetime.now(timezone.utc)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds")


class Store:
    """시간별 gzip jsonl 저장 (각 기록을 독립 gzip 멤버로 추가: 중간 종료에도 읽힘)."""

    def __init__(self, out):
        self.out = Path(out)
        for d in ("public", "routing", "generation"):
            (self.out / d).mkdir(parents=True, exist_ok=True)
        self.counts = defaultdict(Counter)

    def write(self, kind, rec):
        ts = rec.get("request_ts") or time.time()
        hour = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H")
        path = self.out / kind / f"{hour}.jsonl.gz"
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _lock:
            with gzip.open(path, "at", encoding="utf-8") as fh:
                fh.write(line)
            day = hour[:8]
            self.counts[day][(kind, rec.get("api", rec.get("model_key")), rec.get("status"))] += 1

    def event(self, msg):
        line = f"{utcnow().isoformat(timespec='seconds')} {msg}"
        print(line, flush=True)
        with _lock:
            with open(self.out / "events.log", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def manifest(self, day=None):
        days = [day] if day else sorted(self.counts)
        for d in days:
            files = {}
            for kind in ("public", "routing", "generation"):
                for p in sorted((self.out / kind).glob(f"{d}T*.jsonl.gz")):
                    files[f"{kind}/{p.name}"] = {"bytes": p.stat().st_size,
                                                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            c = self.counts.get(d, Counter())
            man = {"day_utc": d, "written_utc": utcnow().isoformat(timespec="seconds"),
                   "counts": [{"kind": k[0], "api_or_model": k[1], "status": k[2], "n": n}
                              for k, n in sorted(c.items(), key=lambda x: str(x[0]))],
                   "files": files}
            (self.out / f"manifest_{d}.json").write_text(json.dumps(man, ensure_ascii=False, indent=2),
                                                         encoding="utf-8")


def http(method, url, body=None, headers=None, timeout=60):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return t, r.status, dict(r.headers.items()), r.read().decode("utf-8", "replace"), None, time.time() - t
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
        return t, e.code, dict(e.headers.items()) if e.headers else {}, b, f"HTTPError {e.code}", time.time() - t
    except Exception as e:
        return t, None, {}, "", f"{type(e).__name__}: {e}", time.time() - t


# ---------------------------------------------------------------- 공개 지표 수집
class PublicCollector:
    """28번 3·4절의 주기대로 공개 GET 수집. 1분 작업은 별도 스레드(다른 작업에 밀리지 않게)."""

    def __init__(self, store, cfg):
        self.s, self.cfg = store, cfg
        self.endpoint_ids = {m["key"]: [] for m in MODELS}     # stats/endpoint 최신 목록 (요청 수 내림차순)
        self.provider_slugs = set()
        self.fail = Counter()
        self.backoff_until = {}

    # -- 요청 하나
    def get(self, api, model_key, path):
        if self.backoff_until.get(api, 0) > time.time():
            return None
        t, st, h, body, err, dur = http("GET", BASE + path, timeout=40)
        rec = {"api": api, "model_key": model_key, "url": BASE + path, "request_utc": iso(t), "request_ts": t,
               "elapsed_s": round(dur, 3), "status": st, "error": err, "headers": h, "body": body}
        self.s.write("public", rec)
        if st is None or st == 429 or st >= 500:
            self.fail[api] += 1
            if self.fail[api] >= 3:
                self.backoff_until[api] = time.time() + 900
                self.s.event(f"ALERT {api} 연속 {self.fail[api]}회 실패(최근 {st} {err}) → 15분 수집 중지")
                self.fail[api] = 0
        else:
            self.fail[api] = 0
        time.sleep(self.cfg["public_pause_s"])
        return rec

    @staticmethod
    def q(m, extra=""):
        return f"permaslug={m['permaslug']}&variant=standard{extra}&_cb={int(time.time())}"

    # -- 작업 정의
    def task_1min(self):
        for m in MODELS:
            self.get("models-endpoints", m["key"], f"/api/v1/models/{m['api_id']}/endpoints")

    def task_5min(self):
        for m in MODELS:
            rec = self.get("endpoint", m["key"], f"/api/frontend/v1/stats/endpoint?{self.q(m)}")
            if rec and rec["status"] == 200:
                try:
                    d = json.loads(rec["body"])["data"]
                    d.sort(key=lambda e: -((e.get("stats") or {}).get("request_count") or 0))
                    self.endpoint_ids[m["key"]] = [e["id"] for e in d]
                    for e in d[:15]:
                        self.provider_slugs.add((e.get("provider_slug") or "").split("/")[0])
                except (ValueError, KeyError, TypeError):
                    pass
            self.get("model-uptime-recent", m["key"], f"/api/frontend/v1/stats/model-uptime-recent?{self.q(m)}")

    def task_10min(self):
        for m in MODELS:
            self.get("effective-pricing", m["key"], f"/api/frontend/v1/stats/effective-pricing?{self.q(m)}")

    def task_15min(self):
        for m in MODELS:
            for api, extra in (("tool-call-error-rate", "&timeRange=3d"),
                               ("structured-output-error-rate", "&timeRange=3d"),
                               ("cache-hit-rate-comparison", "&timeRange=3d"),
                               ("throughput-comparison", "&timeRange=3d&colo=ICN"),
                               ("latency-comparison", "&timeRange=3d&colo=ICN")):
                self.get(api, m["key"], f"/api/frontend/v1/stats/{api}?{self.q(m, extra)}")
            for eid in self.endpoint_ids[m["key"]][: self.cfg["top_uptime_hourly_per_model"]]:
                self.get("uptime-hourly", m["key"], f"/api/frontend/v1/stats/uptime-hourly?id={eid}")

    def task_hourly(self):
        top = self.cfg["top_uptime_hourly_per_model"]
        for m in MODELS:
            for eid in self.endpoint_ids[m["key"]][top:]:
                self.get("uptime-hourly", m["key"], f"/api/frontend/v1/stats/uptime-hourly?id={eid}")
            self.get("listed-pricing", m["key"], f"/api/frontend/v1/stats/listed-pricing?{self.q(m)}")
            self.get("uptime-comparison", m["key"], f"/api/frontend/v1/stats/uptime-comparison?{self.q(m)}")
        for slug in sorted(s for s in self.provider_slugs if s):
            self.get("provider-token-chart", None, f"/api/frontend/v1/stats/provider-token-chart?provider={slug}")

    def task_daily(self):
        for m in MODELS:
            self.get("model-activity", m["key"], f"/api/frontend/v1/stats/model-activity?{self.q(m)}")
            self.get("benchmark-scores", m["key"], f"/api/frontend/v1/stats/benchmark-scores?{self.q(m)}")

    def once(self):
        """점검용: 모든 작업을 한 번씩."""
        for f in (self.task_5min, self.task_1min, self.task_10min, self.task_15min, self.task_hourly,
                  self.task_daily):
            self.s.event(f"once: {f.__name__}")
            f()

    def run(self, end_ts):
        fast_q, slow_q = queue.Queue(), queue.Queue()

        def worker(qq):
            while not STOP.is_set():
                try:
                    f = qq.get(timeout=1)
                except queue.Empty:
                    continue
                try:
                    f()
                except Exception as e:  # 수집은 계속
                    self.s.event(f"ERROR {f.__name__}: {type(e).__name__}: {e}")

        threading.Thread(target=worker, args=(fast_q,), daemon=True).start()
        threading.Thread(target=worker, args=(slow_q,), daemon=True).start()
        # 시작 직후 목록·하루 작업 1회
        slow_q.put(self.task_5min)
        slow_q.put(self.task_daily)
        last_slot = None
        hh, mm = map(int, self.cfg["daily_utc_hhmm"].split(":"))
        while not STOP.is_set() and time.time() < end_ts:
            now = utcnow()
            slot = now.strftime("%Y%m%d%H%M")
            if slot != last_slot:
                last_slot = slot
                mi = now.minute
                fast_q.put(self.task_1min)
                if mi % 5 == 0:
                    slow_q.put(self.task_5min)
                if mi % 10 == 0:
                    slow_q.put(self.task_10min)
                if mi % 15 == 0:
                    slow_q.put(self.task_15min)
                if mi == self.cfg["hourly_offset_min"]:
                    slow_q.put(self.task_hourly)
                if now.hour == hh and mi == mm:
                    slow_q.put(self.task_daily)
                if now.hour == 0 and mi == 1:   # 전날 manifest
                    self.s.manifest((now - timedelta(days=1)).strftime("%Y%m%d"))
            time.sleep(1)


# ---------------------------------------------------------------- 기본 라우팅 호출
class Router:
    def __init__(self, store, cfg, run_id, key=None, dry=False):
        self.s, self.cfg, self.run_id, self.key, self.dry = store, cfg, run_id, key, dry
        self.gen_heap = []
        self.gen_lock = threading.Lock()
        self.spent = 0.0
        self.halted = False
        self.seq = Counter()

    def body(self, m, seq, fixed_prompt=False):
        tag = "fixed" if fixed_prompt else f"{self.run_id}-{m['key']}-{seq:06d}"
        b = {"model": m["api_id"],
             "messages": [{"role": "system", "content": f"Request {tag}. Answer in one short word."},
                          {"role": "user", "content": f"[{tag}] Reply with the single word OK."}],
             "max_tokens": self.cfg["max_tokens"], "stream": False}
        if self.cfg.get("reasoning"):
            b["reasoning"] = self.cfg["reasoning"]
        return b

    def call(self, m, seq, planned_ts, label="main", fixed_prompt=False):
        if self.halted:
            return
        b = self.body(m, seq, fixed_prompt)
        rec = {"label": label, "model_key": m["key"], "seq": seq, "planned_utc": iso(planned_ts),
               "request_body": b}
        if self.dry:
            rec.update({"request_ts": time.time(), "status": "dry-run"})
            self.s.write("routing", rec)
            return
        t, st, h, body, err, dur = http("POST", BASE + "/api/v1/chat/completions", body=b,
                                       headers={"Authorization": f"Bearer {self.key}"}, timeout=180)
        rid = None
        try:
            j = json.loads(body)
            rid = j.get("id")
            rec["provider"] = j.get("provider")
            rec["usage"] = j.get("usage")
        except ValueError:
            pass
        rec.update({"request_utc": iso(t), "request_ts": t, "elapsed_s": round(dur, 3), "status": st,
                    "error": err, "headers": h, "response_body": body, "resp_id": rid})
        self.s.write("routing", rec)
        if rid:
            with self.gen_lock:
                heapq.heappush(self.gen_heap, (t + self.cfg["generation_delay_s"], 0, rid, m["key"], label))

    def generation_worker(self, end_ts):
        while not STOP.is_set() and (time.time() < end_ts + 900 or self.gen_heap):
            with self.gen_lock:
                item = self.gen_heap[0] if self.gen_heap and self.gen_heap[0][0] <= time.time() else None
                if item:
                    heapq.heappop(self.gen_heap)
            if not item:
                time.sleep(1)
                continue
            due, tries, rid, mk, label = item
            t, st, h, body, err, dur = http("GET", f"{BASE}/api/v1/generation?id={rid}",
                                           headers={"Authorization": f"Bearer {self.key}"}, timeout=40)
            if st == 200:
                rec = {"label": label, "model_key": mk, "resp_id": rid, "request_utc": iso(t), "request_ts": t,
                       "status": st, "body": body}
                self.s.write("generation", rec)
                try:
                    cost = json.loads(body)["data"].get("total_cost") or 0
                except (ValueError, KeyError, TypeError, AttributeError):
                    cost = 0
                self.spent += float(cost)
                if self.spent > self.cfg["max_usd"] and not self.halted:
                    self.halted = True
                    self.s.event(f"BUDGET 누적 비용 ${self.spent:.4f} > 상한 ${self.cfg['max_usd']} → 호출 중단")
            elif tries < 10:
                with self.gen_lock:
                    heapq.heappush(self.gen_heap, (time.time() + 30, tries + 1, rid, mk, label))
            else:
                self.s.write("generation", {"label": label, "model_key": mk, "resp_id": rid, "request_ts": t,
                                            "request_utc": iso(t), "status": st, "error": err, "body": body})
            time.sleep(0.2)

    def hour_plan(self, hour_start):
        """그 시간의 호출 계획: 모델별 200회를 10분 창에 고르게, 모델끼리 엇갈리게."""
        n = self.cfg["calls_per_model_per_hour"]
        w0 = hour_start + self.cfg["window_start_min"] * 60
        span = self.cfg["window_minutes"] * 60
        plan = []
        for mi, m in enumerate(MODELS):
            step = span / n
            for i in range(n):
                plan.append((w0 + i * step + mi * step / len(MODELS), m))
        plan.sort(key=lambda x: x[0])
        return plan

    def run(self, start_ts, end_ts):
        if not self.dry:
            threading.Thread(target=self.generation_worker, args=(end_ts,), daemon=True).start()
        pool = ThreadPoolExecutor(max_workers=48)
        hour = start_ts - (start_ts % 3600)
        while hour < end_ts and not STOP.is_set() and not self.halted:
            plan = [p for p in self.hour_plan(hour) if start_ts <= p[0] < end_ts]
            if plan:
                self.s.event(f"hour {iso(hour)} 호출 {len(plan)}건 계획")
            for ts, m in plan:
                if STOP.is_set() or self.halted:
                    break
                wait = ts - time.time()
                if self.dry:
                    pass        # 계획 점검: 기다리지 않음
                elif wait > 0:
                    time.sleep(wait)
                elif wait < -60:
                    self.s.event(f"SKIP {m['key']} 계획 {iso(ts)} (1분 넘게 늦음)")
                    continue
                self.seq[m["key"]] += 1
                pool.submit(self.call, m, self.seq[m["key"]], ts)
            hour += 3600
        pool.shutdown(wait=True)
        if not self.dry:
            while self.gen_heap and not STOP.is_set():
                time.sleep(5)


# ---------------------------------------------------------------- 요약
def read_gz(folder):
    for p in sorted(Path(folder).glob("*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def summarize(out):
    out = Path(out)
    gens = {}
    for g in read_gz(out / "generation"):
        try:
            gens[g["resp_id"]] = json.loads(g["body"])["data"]
        except (ValueError, KeyError, TypeError):
            pass
    hours = defaultdict(lambda: {"sent": 0, "ok": 0, "first": Counter(), "err_status": Counter(), "cost": 0.0})
    for r in read_gz(out / "routing"):
        if r.get("status") == "dry-run":
            continue
        k = (r.get("label"), r["model_key"], (r.get("request_utc") or "")[:13])
        h = hours[k]
        h["sent"] += 1
        h["ok"] += r.get("status") == 200
        g = gens.get(r.get("resp_id"))
        if g:
            pr = g.get("provider_responses") or []
            if pr:
                h["first"][pr[0].get("provider_name") or pr[0].get("endpoint_id")] += 1
            for a in pr:
                if a.get("status") not in (200, None):
                    h["err_status"][a.get("status")] += 1
            h["cost"] += float(g.get("total_cost") or 0)
    print("label | model | hour(UTC) | 보냄 | 성공 | 첫 시도 상위 3 | 오류 상태 | 비용$")
    for k in sorted(hours, key=str):
        h = hours[k]
        print(" | ".join(map(str, [*k, h["sent"], h["ok"], h["first"].most_common(3), dict(h["err_status"]),
                                   round(h["cost"], 5)])))
    pub = Counter()
    for r in read_gz(out / "public"):
        pub[(r["api"], (r.get("request_utc") or "")[:13], r.get("status"))] += 1
    print("\n공개 API 수집 건수 (api, 시간, 상태):")
    for k in sorted(pub, key=str):
        print(" ", k, pub[k])


# ---------------------------------------------------------------- 사전 시험
def pretest(store, cfg, key, n):
    """모델별: 고유 프롬프트 n회, 같은 프롬프트 n회. 30초 뒤 generation 회수 후 요약."""
    r = Router(store, cfg, run_id="pre" + utcnow().strftime("%H%M%S"), key=key)
    for m in MODELS:
        for i in range(n):
            r.call(m, i + 1, time.time(), label="pretest-unique")
            time.sleep(1)
        for i in range(n):
            r.call(m, 1000 + i, time.time(), label="pretest-fixed", fixed_prompt=True)
            time.sleep(1)
    end = time.time()
    th = threading.Thread(target=r.generation_worker, args=(end - 900,), daemon=True)
    th.start()
    while r.gen_heap and time.time() < end + 600:
        time.sleep(3)
    store.event(f"pretest 끝: generation 누적 비용 ${r.spent:.5f}")
    summarize(store.out)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["collect", "route", "run", "pretest", "summarize"])
    ap.add_argument("--out", required=True, help="출력 폴더")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--start-utc", help="시작 시각 (예: 2026-11-21T00:00). 없으면 다음 정시")
    ap.add_argument("--once", action="store_true", help="collect: 모든 공개 작업을 한 번씩만")
    ap.add_argument("--dry-run", action="store_true", help="route/run: 호출하지 않고 계획만 기록")
    ap.add_argument("--n", type=int, default=5, help="pretest: 모델별 횟수")
    ap.add_argument("--max-usd", type=float, default=DEFAULTS["max_usd"])
    ap.add_argument("--max-tokens", type=int, default=DEFAULTS["max_tokens"])
    ap.add_argument("--calls-per-hour", type=int, default=DEFAULTS["calls_per_model_per_hour"])
    a = ap.parse_args()

    cfg = dict(DEFAULTS, max_usd=a.max_usd, max_tokens=a.max_tokens, calls_per_model_per_hour=a.calls_per_hour)
    if a.cmd == "summarize":
        summarize(a.out)
        return
    store = Store(a.out)
    (Path(a.out) / "config.json").write_text(json.dumps({"cmd": a.cmd, "argv": sys.argv[1:], "cfg": cfg,
                                                         "models": MODELS, "user_agent": UA,
                                                         "written_utc": utcnow().isoformat()},
                                                        ensure_ascii=False, indent=2), encoding="utf-8")
    key = os.environ.get("OPENROUTER_API_KEY")
    needs_key = a.cmd in ("route", "run", "pretest") and not a.dry_run
    if needs_key and not key:
        sys.exit("OPENROUTER_API_KEY 환경변수가 필요합니다 (키 값은 기록하지 않음).")

    if a.cmd == "collect" and a.once:
        PublicCollector(store, cfg).once()
        store.manifest()
        return
    if a.cmd == "pretest":
        pretest(store, cfg, key, a.n)
        store.manifest()
        return

    if a.start_utc:
        start = datetime.fromisoformat(a.start_utc).replace(tzinfo=timezone.utc).timestamp()
    else:
        start = (time.time() // 3600 + 1) * 3600
    end = start + a.hours * 3600
    store.event(f"{a.cmd} 시작 예정 {iso(start)} ~ {iso(end)}, 설정 {json.dumps(cfg, ensure_ascii=False)}")
    threads = []
    if a.cmd in ("collect", "run"):
        pc = PublicCollector(store, cfg)
        # 수집은 시작 10분 전부터 (첫 호출 전에 지표가 있도록)
        pre = max(0, start - 600 - time.time())
        def run_pc():
            time.sleep(pre)
            pc.run(end)
        threads.append(threading.Thread(target=run_pc, daemon=True))
    if a.cmd in ("route", "run"):
        rt = Router(store, cfg, run_id=datetime.fromtimestamp(start, timezone.utc).strftime("r%Y%m%d%H"),
                    key=key, dry=a.dry_run)
        threads.append(threading.Thread(target=rt.run, args=(start, end), daemon=True))
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(5)
    except KeyboardInterrupt:
        STOP.set()
        store.event("사용자 중단")
    store.manifest()
    store.event("끝")


if __name__ == "__main__":
    main()
