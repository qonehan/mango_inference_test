#!/usr/bin/env python3
"""실험 원본(요청별 기록·공개 지표 스냅숏)을 엑셀로 펼친다 (2026-11-23, 사용자 요청).

원본은 그대로 두고 읽기만 한다. 엑셀 셀 한도(32,767자)를 넘는 문자열은 잘라 표시하고 `…[잘림]`을 붙인다.

사용:
  python export_raw_xlsx_20261123.py requests21 --run <run_20261120> --long <long 폴더>... --out <xlsx>
  python export_raw_xlsx_20261123.py requests22 --run <서울 run> --run <미국 run> --out <xlsx>
  python export_raw_xlsx_20261123.py public --pub <public 폴더> [--pub ...] --out <xlsx>

공개 지표 시트
- 엔드포인트통계_5분: /api/frontend/v1/stats/endpoint (30분 창 TTFT·처리 속도 분위수, 요청 수, 게시가)
- 엔드포인트목록_1분: /api/v1/models/.../endpoints (상태, 가동률 5분·30분·1일, 30분 지연·처리량)
- 시간칸_지표: 1시간 칸 시계열(도구 호출·구조화 출력 오류율, 캐시 적중률, 처리량·지연 비교(colo별), 일별 가동률 비교).
  같은 칸을 여러 번 수집하므로 (지표, 모델, colo, 칸, 엔드포인트)마다 **마지막 수집값**과 수집 횟수를 둔다.
- 실효가격_10분, 모델가용률_10분칸, 엔드포인트가동률_시간칸, 게시가이력, 엔드포인트_식별표
"""
import argparse
import glob
import gzip
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

import xlsxwriter

MAXC = 32000


def rd(folder):
    files = sorted(glob.glob(os.path.join(folder, "*.jsonl.gz"))) + sorted(glob.glob(os.path.join(folder, "*.jsonl")))
    for p in files:
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        pass


def utc(ts):
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except (TypeError, ValueError):
        return None


def cell(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    if isinstance(v, str):
        return v if len(v) <= MAXC else v[:MAXC] + "…[잘림]"
    if isinstance(v, bool):
        return str(v)
    return v


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def body(r):
    b = r.get("body")
    if isinstance(b, str):
        try:
            return json.loads(b)
        except ValueError:
            return None
    return b


class Book:
    def __init__(self, path):
        self.wb = xlsxwriter.Workbook(path, {"constant_memory": True, "strings_to_numbers": False,
                                            "strings_to_urls": False})
        self.hdr = self.wb.add_format({"bold": True, "bg_color": "#E8EEEC", "border": 1})
        self.counts = {}

    def sheet(self, name, cols, rows):
        ws = self.wb.add_worksheet(name)
        for j, c in enumerate(cols):
            ws.write(0, j, c, self.hdr)
            ws.set_column(j, j, min(max(len(c) + 4, 10), 40))
        ws.freeze_panes(1, 0)
        n = 0
        for row in rows:
            n += 1
            if n >= 1_048_575:
                print("  행 한도 도달:", name, file=sys.stderr)
                break
            for j, v in enumerate(row):
                v = cell(v)
                if v is not None:
                    ws.write(n, j, v)
        ws.autofilter(0, 0, max(n, 1), len(cols) - 1)
        self.counts[name] = n
        print(f"  {name}: {n:,}행")

    def readme(self, lines):
        ws = self.wb.add_worksheet("설명")
        ws.set_column(0, 0, 150)
        for i, l in enumerate(lines):
            ws.write(i, 0, l)

    def close(self):
        self.wb.close()


# ------------------------------------------------------------------ 2-1 요청
def gen_index(folder):
    g = {}
    for r in rd(os.path.join(folder, "generation")):
        b = body(r)
        d = (b or {}).get("data") if isinstance(b, dict) else None
        if d and r.get("resp_id"):
            g[r["resp_id"]] = d
    return g


def req21_rows(folder, tag):
    gens = gen_index(folder)
    for r in rd(os.path.join(folder, "routing")):
        rb = r.get("response_body")
        try:
            resp = json.loads(rb.strip()) if isinstance(rb, str) and rb.strip().startswith("{") else {}
        except ValueError:
            resp = {}
        u = resp.get("usage") or {}
        g = gens.get(r.get("resp_id")) or {}
        pr = g.get("provider_responses") or []
        first = pr[0] if pr else {}
        req = r.get("request_body")
        try:
            reqd = json.loads(req) if isinstance(req, str) and req.startswith("{") else None
        except ValueError:
            reqd = None
        yield [tag, r.get("label"), r.get("model_key"), r.get("seq"), r.get("planned_utc"), r.get("request_utc"),
               num(r.get("elapsed_s")), r.get("status"), r.get("error"), r.get("provider") or resp.get("provider"),
               first.get("endpoint_id"), first.get("provider_name"), first.get("status"), first.get("latency"), len(pr),
               "; ".join(f"{p.get('provider_name')}:{p.get('status')}:{p.get('latency')}" for p in pr),
               u.get("prompt_tokens"), u.get("completion_tokens"),
               (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
               (u.get("prompt_tokens_details") or {}).get("cached_tokens"), u.get("cost"),
               g.get("latency"), g.get("generation_time"), g.get("native_tokens_prompt"), g.get("native_tokens_completion"),
               g.get("native_tokens_reasoning"), g.get("native_tokens_cached"), g.get("finish_reason"), g.get("total_cost"),
               g.get("provider_name"), r.get("resp_id"),
               (resp.get("choices") or [{}])[0].get("message", {}).get("content") if resp.get("choices") else None,
               json.dumps(reqd, ensure_ascii=False) if reqd else req]


REQ21_COLS = ["폴더", "label", "모델", "seq", "계획시각_UTC", "요청시각_UTC", "응답시간_s", "HTTP상태", "오류",
              "최종제공사", "첫시도_endpoint_id", "첫시도_제공사", "첫시도_상태", "첫시도_지연ms", "시도수", "시도내역(제공사:상태:지연)",
              "prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens", "usage_cost_USD",
              "gen_latency_ms", "gen_generation_time_ms", "native_prompt", "native_completion", "native_reasoning",
              "native_cached", "finish_reason", "gen_total_cost_USD", "gen_provider", "resp_id", "응답본문", "요청본문"]


def cmd_requests21(a):
    bk = Book(a.out)
    bk.readme(["실험 2-1회 요청별 원본 (OpenRouter 기본 라우팅 호출)",
               "원본: 03_MangoBoost/90_작업자료/실험_2-1회_20261120/vm_결과_20261122/ (run_20261120 = 본 실험, long* = 긴 입력 대조군)",
               "한 행 = 요청 1건. 첫시도_* 는 generation API의 provider_responses[0] (라우터가 처음 고른 엔드포인트). 분석은 label=main, HTTP 200만 사용",
               "endpoint_id → 제공사 태그는 공개 지표 파일의 '엔드포인트_식별표' 시트로 연결",
               "usage_cost는 응답의 usage.cost, gen_total_cost는 generation API의 total_cost (USD)",
               f"생성: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, export_raw_xlsx_20261123.py"])

    def rows():
        for f in a.run:
            yield from req21_rows(f, os.path.basename(f.rstrip("/\\")))
    bk.sheet("요청_본실험", REQ21_COLS, rows())
    if a.long:
        def lrows():
            for f in a.long:
                yield from req21_rows(f, os.path.basename(f.rstrip("/\\")))
        bk.sheet("요청_긴입력대조군", REQ21_COLS, lrows())
    bk.close()


# ------------------------------------------------------------------ 2-2 요청
REQ22_COLS = ["VM", "run", "round", "label", "kind", "seq", "rep", "모델", "host", "경로(tag)", "pid", "item_id", "nonce",
              "요청시각_UTC", "ok", "fail_side", "error", "status", "error_detail", "finish_reason",
              "ttft_any_ms", "ttft_content_ms", "e2e_ms", "decode_tps", "decode_tps_any", "total_tps",
              "prompt_tokens", "cached_tokens", "completion_tokens", "reasoning_tokens", "reasoning_chars", "n_chunks",
              "새연결", "tcp_ms", "tls_ms", "cost_est_USD", "judge_outcome", "judge_strict_ok", "judge_lenient_ok",
              "judge_상세", "tool_name", "tool_calls", "응답본문", "resp_id", "system_fingerprint",
              "gen_provider", "gen_total_cost_USD", "gen_latency_ms", "gen_generation_time_ms", "gen_native_prompt",
              "gen_native_completion", "gen_native_reasoning", "gen_native_cached", "request_id"]


def cmd_requests22(a):
    bk = Book(a.out)
    bk.readme(["실험 2-2회 요청별 원본 (Mango 직접 호출 + OR 고정 경로 호출)",
               "원본: 각 VM /opt/mango-bench/exp2_2/run/records/*.jsonl (round_<vm>_r1~r4 = S·L·G 회차, tj_* = T·J), run/generations.jsonl",
               "kind: S 짧은 질문(본문 TTFT), L 긴 입력 약 3,100토큰, G 생성 약 1,000토큰 입력(출력 속도), T 도구 호출, J 구조화 출력, warmup 예열(분석 제외)",
               "host=mango 는 Mango API 직접, host=or 는 OpenRouter provider.order=[tag], allow_fallbacks=false. pid의 anchor:* 는 T·J 기준점 경로",
               "cost_est는 Mango 정가 × 토큰, OR은 단가표 × 토큰(기준점은 단가표에 없어 0). 실제 OR 청구는 gen_total_cost",
               "스트리밍 조각(chunks)·응답 헤더는 크기 때문에 넣지 않았다(원본 JSONL에 있음)",
               f"생성: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, export_raw_xlsx_20261123.py"])

    def rows():
        for run in a.run:
            gens = {}
            gp = os.path.join(run, "generations.jsonl")
            if os.path.exists(gp):
                for line in open(gp, encoding="utf-8"):
                    try:
                        g = json.loads(line)
                    except ValueError:
                        continue
                    d = g.get("data") or (body(g) or {}).get("data") if isinstance(g, dict) else None
                    rid = g.get("resp_id") or (d or {}).get("id")
                    if rid and d:
                        gens[rid] = d
            for r in rd(os.path.join(run, "records")):
                j = r.get("judge") or {}
                g = gens.get(r.get("resp_id")) or {}
                c = r.get("conn") or {}
                yield [r.get("vm"), r.get("run"), r.get("round"), r.get("label"), r.get("kind"), r.get("seq"), r.get("rep"),
                       r.get("mk"), r.get("host"), r.get("tag"), r.get("pid"), r.get("item_id"), r.get("nonce"),
                       utc(r.get("t_start_utc")), r.get("ok"), r.get("fail_side"), r.get("error"), r.get("status"),
                       r.get("error_detail"), r.get("finish_reason"), r.get("ttft_any_ms"), r.get("ttft_content_ms"),
                       r.get("e2e_ms"), r.get("decode_tps"), r.get("decode_tps_any"), r.get("total_tps"),
                       r.get("prompt_tokens"), r.get("cached_tokens"), r.get("completion_tokens"), r.get("reasoning_tokens"),
                       r.get("reasoning_chars"), r.get("n_chunks"), c.get("new_connection"), c.get("tcp_ms"), c.get("tls_ms"),
                       r.get("cost_est"), j.get("outcome"), j.get("strict_ok"), j.get("lenient_ok"),
                       {k: v for k, v in j.items() if k not in ("outcome", "strict_ok", "lenient_ok")} or None,
                       r.get("tool_name"), r.get("tool_calls") or None, r.get("text"), r.get("resp_id"),
                       r.get("system_fingerprint"), g.get("provider_name"), g.get("total_cost"), g.get("latency"),
                       g.get("generation_time"), g.get("native_tokens_prompt"), g.get("native_tokens_completion"),
                       g.get("native_tokens_reasoning"), g.get("native_tokens_cached"), r.get("request_id")]
    bk.sheet("요청", REQ22_COLS, rows())
    bk.close()


# ------------------------------------------------------------------ 공개 지표
def cmd_public(a):
    bk = Book(a.out)
    ep_rows, me_rows, eff_rows = [], [], []
    cells = {}            # (api, model, colo, x, key) -> [마지막 수집, 값, 횟수]
    mu, uh, lp, idmap = {}, {}, {}, {}
    ptc = {}
    for pub in a.pub:
        for r in rd(pub):
            if r.get("status") != 200:
                continue
            api, mk, t = r.get("api"), r.get("model_key"), r.get("request_ts")
            b = body(r)
            if b is None:
                continue
            d = b.get("data", b) if isinstance(b, dict) else b
            ct = utc(t)
            colo = (re.search(r"colo=([A-Z]+)", r.get("url") or "") or [None, None])[1]
            if api == "endpoint" and isinstance(d, list):
                for x in d:
                    s, p = x.get("stats") or {}, x.get("pricing") or {}
                    idmap[x["id"]] = (mk, x.get("provider_name"), x.get("provider_slug"), x.get("quantization"))
                    ep_rows.append([ct, mk, x["id"], x.get("provider_name"), x.get("provider_slug"), x.get("quantization"),
                                    x.get("status"), x.get("is_deranked"), x.get("is_disabled"), x.get("context_length"),
                                    num(p.get("prompt")), num(p.get("completion")), num(p.get("input_cache_read")),
                                    s.get("p50_latency"), s.get("p75_latency"), s.get("p90_latency"), s.get("p95_latency"),
                                    s.get("p99_latency"), s.get("mean_latency"), s.get("p50_throughput"), s.get("p75_throughput"),
                                    s.get("p90_throughput"), s.get("p95_throughput"), s.get("p99_throughput"),
                                    s.get("request_count"), s.get("latency_request_count"), s.get("throughput_request_count"),
                                    s.get("window_minutes"), s.get("latency_metric")])
            elif api == "models-endpoints" and isinstance(d, dict):
                for x in d.get("endpoints") or []:
                    pr = x.get("pricing") or {}
                    me_rows.append([ct, mk, x.get("provider_name"), x.get("tag"), x.get("quantization"), x.get("status"),
                                    x.get("uptime_last_5m"), x.get("uptime_last_30m"), x.get("uptime_last_1d"),
                                    x.get("latency_last_30m") if not isinstance(x.get("latency_last_30m"), dict) else json.dumps(x.get("latency_last_30m")),
                                    x.get("throughput_last_30m") if not isinstance(x.get("throughput_last_30m"), dict) else json.dumps(x.get("throughput_last_30m")),
                                    num(pr.get("prompt")), num(pr.get("completion")), num(pr.get("input_cache_read")),
                                    x.get("context_length"), x.get("max_completion_tokens"),
                                    ",".join(x.get("supported_parameters") or [])])
            elif api == "effective-pricing" and isinstance(d, dict):
                for p in d.get("providerSummaries") or []:
                    eff_rows.append([ct, mk, p.get("endpointId"), p.get("providerName"), p.get("providerSlug"),
                                     p.get("effectiveInputPrice"), p.get("effectiveOutputPrice"), p.get("cacheHitRate"),
                                     p.get("totalTokens") if "totalTokens" in p else None,
                                     json.dumps({k: v for k, v in p.items() if k not in ("endpointId", "providerName", "providerSlug", "effectiveInputPrice", "effectiveOutputPrice", "cacheHitRate", "totalTokens")}, ensure_ascii=False),
                                     d.get("weightedInputPrice"), d.get("weightedOutputPrice"), d.get("weightedCacheHitRate")])
            elif api in ("tool-call-error-rate", "structured-output-error-rate", "cache-hit-rate-comparison",
                         "throughput-comparison", "latency-comparison", "uptime-comparison") and isinstance(d, list):
                for c in d:
                    for k, v in (c.get("y") or {}).items():
                        key = (api, mk, colo, c.get("x"), k)
                        o = cells.get(key)
                        cells[key] = [ct, v, (o[2] + 1) if o else 1]
            elif api == "model-uptime-recent" and isinstance(d, dict):
                for bkt in d.get("buckets") or []:
                    mu[(mk, bkt.get("timestamp"))] = [ct, bkt.get("availability"), bkt.get("availabilityWithoutRouting")]
            elif api == "uptime-hourly" and isinstance(d, dict):
                eid = (re.search(r"id=([0-9a-f-]+)", r.get("url") or "") or [None, None])[1]
                for h in d.get("history") or []:
                    uh[(eid, h.get("date"))] = [ct, h.get("uptime"), h.get("judgedMinutes")]
            elif api == "listed-pricing" and isinstance(d, dict):
                for s in d.get("series") or []:
                    for kind in ("input", "output", "cacheRead", "cacheWrite", "discount"):
                        for pt in s.get(kind) or []:
                            lp[(mk, s.get("endpointId"), kind, pt.get("at"))] = [s.get("providerName"), s.get("providerSlug"), pt.get("value")]
            elif api == "provider-token-chart" and isinstance(d, dict):
                prov = (re.search(r"provider=([^&]+)", r.get("url") or "") or [None, None])[1]
                for c in d.get("chartData") or []:
                    for m, v in (c.get("ys") or {}).items():
                        ptc[(prov, c.get("x"), m)] = [ct, v]
    tag = lambda e: (idmap.get(e) or (None, None, None, None))
    bk.readme(["OpenRouter 공개 지표 스냅숏 원본 (실시간 성능 집계)",
               "원본: " + " , ".join(a.pub),
               "엔드포인트통계_5분: stats/endpoint. p50_latency = 공개 TTFT(ms), p50_throughput = 처리 속도(tok/s), 30분 창. 가격은 $/토큰",
               "엔드포인트목록_1분: /api/v1/models/<모델>/endpoints. 가동률(%)과 최근 30분 지연·처리량",
               "시간칸_지표: 1시간(또는 일) 칸 시계열. 같은 칸을 여러 번 수집하므로 칸마다 마지막 수집값과 수집 횟수. colo는 관측 지점(ICN 서울, IAD 미국 동부, 빈칸 = 전체)",
               "  tool-call-error-rate·structured-output-error-rate는 %, cache-hit-rate-comparison은 %, latency-comparison은 ms, throughput-comparison은 tok/s, uptime-comparison은 %",
               "엔드포인트_식별표: endpoint_id → 제공사·태그(provider_slug). 요청 파일의 첫시도_endpoint_id와 연결",
               f"생성: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC, export_raw_xlsx_20261123.py"])
    bk.sheet("엔드포인트통계_5분", ["수집_UTC", "모델", "endpoint_id", "제공사", "태그", "양자화", "status", "is_deranked", "is_disabled",
                                 "context_length", "입력가_USD/토큰", "출력가_USD/토큰", "캐시읽기가_USD/토큰",
                                 "p50_latency_ms", "p75_latency_ms", "p90_latency_ms", "p95_latency_ms", "p99_latency_ms", "mean_latency_ms",
                                 "p50_throughput", "p75_throughput", "p90_throughput", "p95_throughput", "p99_throughput",
                                 "request_count_30분", "latency_request_count", "throughput_request_count", "window_minutes", "latency_metric"], ep_rows)
    del ep_rows
    bk.sheet("엔드포인트목록_1분", ["수집_UTC", "모델", "제공사", "태그", "양자화", "status", "uptime_5m", "uptime_30m", "uptime_1d",
                                 "latency_last_30m", "throughput_last_30m", "입력가", "출력가", "캐시읽기가", "context_length",
                                 "max_completion_tokens", "지원파라미터"], me_rows)
    del me_rows
    bk.sheet("시간칸_지표", ["지표", "모델", "colo", "칸_시각", "endpoint_id", "제공사", "태그", "값", "마지막수집_UTC", "수집횟수"],
             ([k[0], k[1], k[2], k[3], k[4], tag(k[4])[1], tag(k[4])[2], v[1], v[0], v[2]] for k, v in sorted(cells.items(), key=lambda z: tuple(str(x) for x in z[0]))))
    bk.sheet("실효가격_10분", ["수집_UTC", "모델", "endpoint_id", "제공사", "태그", "실효입력가", "실효출력가", "캐시적중률", "totalTokens",
                            "기타필드", "모델가중_입력가", "모델가중_출력가", "모델가중_캐시적중률"], eff_rows)
    bk.sheet("모델가용률_10분칸", ["모델", "칸_시각", "마지막수집_UTC", "availability", "availabilityWithoutRouting"],
             ([k[0], k[1], *v] for k, v in sorted(mu.items())))
    bk.sheet("엔드포인트가동률_시간칸", ["endpoint_id", "모델", "제공사", "태그", "칸_시각", "마지막수집_UTC", "uptime", "judgedMinutes"],
             ([k[0], tag(k[0])[0], tag(k[0])[1], tag(k[0])[2], k[1], *v] for k, v in sorted(uh.items(), key=lambda z: (str(z[0][0]), str(z[0][1])))))
    bk.sheet("게시가이력", ["모델", "endpoint_id", "제공사", "태그", "항목", "적용시각", "값_USD/100만토큰"],
             ([k[0], k[1], v[0], v[1], k[2], k[3], v[2]] for k, v in sorted(lp.items(), key=lambda z: tuple(str(x) for x in z[0]))))
    bk.sheet("제공사토큰_일별", ["제공사", "날짜", "모델", "토큰", "마지막수집_UTC"],
             ([k[0], k[1], k[2], v[1], v[0]] for k, v in sorted(ptc.items(), key=lambda z: tuple(str(x) for x in z[0]))))
    bk.sheet("엔드포인트_식별표", ["endpoint_id", "모델", "제공사", "태그", "양자화"],
             ([e, *v] for e, v in sorted(idmap.items(), key=lambda z: (str(z[1][0]), str(z[1][2])))))
    bk.close()


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("requests21"); p.add_argument("--run", action="append", required=True); p.add_argument("--long", action="append"); p.add_argument("--out", required=True)
    p = sp.add_parser("requests22"); p.add_argument("--run", action="append", required=True); p.add_argument("--out", required=True)
    p = sp.add_parser("public"); p.add_argument("--pub", action="append", required=True); p.add_argument("--out", required=True)
    a = ap.parse_args()
    {"requests21": cmd_requests21, "requests22": cmd_requests22, "public": cmd_public}[a.cmd](a)


if __name__ == "__main__":
    main()
