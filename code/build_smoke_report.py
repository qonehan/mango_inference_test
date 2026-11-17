"""간이 테스트 결과(raw.jsonl + 재시험 폴더)를 합쳐 결과 페이지(HTML)를 만든다.

사용: python build_smoke_report.py <간이테스트 폴더>
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

NAMES = {
    "deepseek-v4.1-flash-1017": ("DeepSeek V4.1 Flash 1017", "deepseek-ai/DeepSeek-V4.1-Flash-1017"),
    "glm-5.3": ("GLM-5.3", "zai-org/GLM-5.3"),
    "glm-5.3-flash": ("GLM-5.3-Flash", "zai-org/GLM-5.3-Flash"),
    "kimi-k3.1-pro": ("Kimi K3.1 Pro", "moonshotai/Kimi-K3.1-Pro"),
}
ARM = {"M": "Mango 직접", "OR-default": "OpenRouter 기본", "OR-sort-price": "정렬: 가격",
       "OR-sort-throughput": "정렬: 처리량", "OR-sort-latency": "정렬: 지연", "OR-floor": ":floor", "OR-nitro": ":nitro"}
e = html.escape


def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def endpoint_map(folder: Path) -> dict:
    m = {}
    for f in [folder / "endpoints.jsonl"] + sorted(folder.glob("*/endpoints.jsonl")):
        if f.exists():
            for r in load(f):
                m[r["request_id"]] = r
    return m


def merged(folder: Path) -> list[dict]:
    emap = endpoint_map(folder)
    final = {}
    for r in load(folder / "raw.jsonl"):
        final[(r["model_key"], r["arm"], r["kind"])] = dict(r, retest=False, first_error=None)
    for sub in sorted(p for p in folder.iterdir() if p.is_dir() and (p / "raw.jsonl").exists()):
        for r in load(sub / "raw.jsonl"):
            k = (r["model_key"], r["arm"], r["kind"])
            prev = final.get(k, {})
            final[k] = dict(r, retest=True, first_error=(prev.get("error") or "")[:90])
    for r in final.values():
        r["ep"] = emap.get(r["request_id"])
    return list(final.values())


def prompts_section() -> str:
    """smoke_test.py에서 실제로 보낸 프롬프트·도구 정의·요청 설정을 그대로 가져와 보여 준다."""
    sys.path.insert(0, str(Path(__file__).parent))
    import smoke_test as st
    text_msg = "ID: 3f9c2a7d1b8e4c60\n" + st.PROMPT
    tool_msg = "ID: 3f9c2a7d1b8e4c60\n" + st.TOOL_PROMPT
    common = ('"stream": true,\n"stream_options": {"include_usage": true},\n'
              '"temperature": 0.6,\n"top_p": 1,\n"max_tokens": 2048')
    return f"""
  <section>
    <h2>실제로 보낸 요청</h2>
    <p class="lede" style="margin:0 0 12px">모든 경로에 같은 내용을 보냈습니다. 첫 줄의 <code>ID:</code>는 요청마다 새로 만든 16자리 무작위 문자열(nonce)로, 경로끼리 캐시가 섞이지 않게 하려는 것입니다. 아래 값은 예시입니다.</p>
    <div class="cards">
      <div class="card"><h3>텍스트 요청 <span class="chip">경로 12개 × 모델 4개</span></h3>
        <div class="label">사용자 메시지</div><pre>{e(text_msg)}</pre>
        <div class="label">성공 기준</div><p>답변이 끝까지 생성됨 (<code>finish_reason = stop</code>)</p></div>
      <div class="card"><h3>도구 호출 요청 <span class="chip">Mango 직접·OpenRouter 기본 × 모델 4개</span></h3>
        <div class="label">사용자 메시지</div><pre>{e(tool_msg)}</pre>
        <div class="label">함께 보낸 도구 정의 (<code>tool_choice: "auto"</code>)</div><pre>{e(json.dumps(st.TOOLS, ensure_ascii=False, indent=2))}</pre>
        <div class="label">성공 기준</div><p>글 대신 <code>read_file</code> 함수 호출을 돌려줌 (<code>finish_reason = tool_calls</code>)</p></div>
    </div>
    <div class="cards" style="margin-top:12px">
      <div class="card"><h3>공통 요청 설정</h3><pre>{e(common)}</pre>
        <p>SDK 자동 재시도는 끔(<code>max_retries=0</code>). OpenRouter 경로에만 요청 ID를 <code>"trace": {{"trace_id": …}}</code>로 추가.</p></div>
      <div class="card"><h3>정렬과 접미사</h3><ul>
        <li><code>sort: "price"</code>: 기본 배분을 건너뛰고 가격이 낮은 엔드포인트부터 시도</li>
        <li><code>:floor</code>: 가격순 정렬 + 할인된 flex 등급 엔드포인트도 후보에 넣음</li>
        <li><code>sort: "throughput"</code>: 처리량이 높은 엔드포인트부터 시도</li>
        <li><code>:nitro</code>: 처리량순 정렬 + 빠른 priority 등급 엔드포인트도 후보에 넣음</li></ul>
        <p>이번 1회 호출에서는 4개 모델 모두 <code>:floor</code>가 가격순과, <code>:nitro</code>가 처리량순과 같은 엔드포인트로 갔습니다.</p></div>
      <div class="card"><h3>경로별로 달라지는 부분</h3><ul>
        <li>Mango 직접: <code>api.mangoboost.io/v1</code>, model = Mango 모델 ID</li>
        <li>OpenRouter 기본: provider 설정 없음</li>
        <li>고정: <code>"provider": {{"order": ["제공사"], "allow_fallbacks": false}}</code></li>
        <li>정렬: <code>"provider": {{"sort": "price" | "throughput" | "latency"}}</code></li>
        <li><code>:floor</code>, <code>:nitro</code>: 모델 이름 뒤에 접미사</li></ul></div>
    </div>
  </section>"""


MANGO_PRICES: dict = {}


def mango_cost(model_id: str, usage: dict):
    """Mango 응답에는 비용이 없어 GET /v1/models의 cost(USD/100만 토큰)로 계산한다."""
    c = MANGO_PRICES.get(model_id)
    if not c or not usage:
        return None
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    prompt = usage.get("prompt_tokens") or 0
    out = usage.get("completion_tokens") or 0
    read = (c.get("cache") or {}).get("read") or c["input"]
    return ((prompt - cached) * c["input"] + cached * read + out * c["output"]) / 1e6


def fmt(v):
    return "–" if v is None else f"{v:,.0f}"


def model_section(key: str, recs: list[dict]) -> str:
    name, mango_id = NAMES[key]
    vmax = max((r.get("e2e_ms") or 0) for r in recs) or 1
    rows = []
    for r in recs:
        u = r.get("usage") or {}
        g = r.get("generation") or {}
        served = g.get("provider_name") or ("Mango" if r["host"] == "mango" else "–")
        chip = '<span class="chip ok">성공</span>' if r["ok"] else '<span class="chip bad">실패</span>'
        if r["retest"]:
            chip += f'<span class="chip re" title="{e(r["first_error"] or "")}">재시험</span>'
        c, t = r.get("ttft_content_ms") or 0, r.get("e2e_ms") or 0
        bar = (f'<div class="bar" aria-hidden="true"><i class="b1" style="width:{c / vmax * 100:.1f}%"></i>'
               f'<i class="b2" style="width:{max(t - c, 0) / vmax * 100:.1f}%"></i></div>')
        ep = r.get("ep") or {}
        att = ep.get("attempts") or []
        last = att[-1] if att else {}
        if r["host"] == "mango":
            ep_txt = "api.mangoboost.io"
        elif last:
            ep_txt = f"<code>{e(last.get('endpoint') or '?')}</code> <span class='q'>{e(last.get('quantization') or '')}</span>"
            if len(att) > 1:
                ep_txt += f" <span class='chip re'>시도 {len(att)}회</span>"
        else:
            ep_txt = "–"
        cost = g.get("total_cost")
        cost_txt = "–" if cost is None else f"${cost:.5f}"
        if r["host"] == "mango":
            mc = mango_cost(mango_id, u)
            cost_txt = "–" if mc is None else f"${mc:.5f}<sup>*</sup>"
        rows.append(
            f"<tr><td>{e(ARM.get(r['arm'], r['arm'].replace('OR-pin-', '고정: ')))}</td>"
            f"<td>{'도구' if r['kind'] == 'tool' else '텍스트'}</td><td>{chip}</td><td>{e(served)}</td><td>{ep_txt}</td>"
            f"<td class='n'>{fmt(r.get('ttft_content_ms'))}</td><td class='n'>{fmt(r.get('e2e_ms'))}</td>"
            f"<td class='barcell'>{bar}</td><td class='n'>{fmt(u.get('completion_tokens'))}</td>"
            f"<td class='n'>{fmt((u.get('completion_tokens_details') or {}).get('reasoning_tokens') if (u.get('completion_tokens_details') or {}).get('reasoning_tokens') is not None else u.get('reasoning_tokens'))}</td>"
            f"<td class='n'>{cost_txt}</td></tr>")
    return f"""
<section class="model">
  <div class="mhead"><h3>{e(name)}</h3><code>Mango ID: {e(mango_id)}</code></div>
  <div class="tw"><table>
    <thead><tr><th>경로</th><th>종류</th><th>결과</th><th>응답 제공사</th><th>엔드포인트 · 양자화</th><th class="n">첫 답변 ms</th>
    <th class="n">전체 ms</th><th>시간 구성</th><th class="n">출력 토큰</th><th class="n">reasoning</th><th class="n">비용</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
</section>"""


CSS = """
/* layout: 한 열 보고서. 요약 수치 → 문제와 조치 → 모델별 경로 표 → 데이터 확보 현황 */
:root {
  --bg: #f6f7f9; --surface: #ffffff; --fg: #18202b; --muted: #5d6877; --line: #dde2e9;
  --ok: #1d7a52; --ok-bg: #e6f4ec; --re: #9a5a12; --re-bg: #fbf0e1; --bad: #b42318; --bad-bg: #fdecea;
  --b1: #2e5fd8; --b2: #b9c8ef;
  --sans: "IBM Plex Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  --mono: "IBM Plex Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #11151b; --surface: #19202a; --fg: #e5e9ef; --muted: #9aa5b4; --line: #2c3542;
  --ok: #5fd3a0; --ok-bg: #173a2c; --re: #f0b46a; --re-bg: #3a2a14; --bad: #ff8a80; --bad-bg: #3d1a17;
  --b1: #7ea2ff; --b2: #34456b; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #11151b; --surface: #19202a; --fg: #e5e9ef; --muted: #9aa5b4; --line: #2c3542;
  --ok: #5fd3a0; --ok-bg: #173a2c; --re: #f0b46a; --re-bg: #3a2a14; --bad: #ff8a80; --bad-bg: #3d1a17;
  --b1: #7ea2ff; --b2: #34456b; color-scheme: dark; }
body { background: var(--bg); color: var(--fg); font-family: var(--sans); font-size: 15px; line-height: 1.6; }
.wrap { max-width: 1080px; margin: 0 auto; padding-block: 32px 56px; padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px)); display: grid; gap: 28px; }
h1 { font-size: 1.7rem; margin: 0; text-wrap: balance; }
h2 { font-size: 1.15rem; margin: 0 0 10px; }
h3 { font-size: 1rem; margin: 0; }
p { margin: 0; max-width: 70ch; }
.lede { color: var(--muted); margin-top: 6px; }
.note { font-size: .85rem; color: var(--re); background: var(--re-bg); padding: 8px 12px; border-radius: 6px; margin-top: 12px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.stat { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; }
.stat b { display: block; font-size: 1.6rem; font-variant-numeric: tabular-nums; line-height: 1.2; }
.stat span { font-size: .82rem; color: var(--muted); }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; display: grid; gap: 6px; align-content: start; }
.card h3 { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.card p { font-size: .9rem; }
.fix { color: var(--ok); font-weight: 500; }
.card ul { margin: 0; padding-left: 18px; font-size: .9rem; }
.card li { margin: 3px 0; }
.models { display: grid; gap: 14px; }
.model { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 16px; min-width: 0; }
.mhead { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; margin-bottom: 10px; }
code { font-family: var(--mono); font-size: .8rem; color: var(--muted); }
.tw { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .86rem; min-width: 980px; }
.q { font-size: .72rem; color: var(--muted); }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
th { font-weight: 500; color: var(--muted); font-size: .78rem; letter-spacing: .02em; }
.n { text-align: right; font-variant-numeric: tabular-nums; font-family: var(--mono); font-size: .82rem; }
.chip { display: inline-block; font-size: .72rem; font-weight: 500; padding: 1px 8px; border-radius: 99px; margin-right: 4px; }
.chip.ok { color: var(--ok); background: var(--ok-bg); }
.chip.re { color: var(--re); background: var(--re-bg); cursor: help; }
.chip.bad { color: var(--bad); background: var(--bad-bg); }
.bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; background: var(--bg); width: 170px; }
.bar i { display: block; height: 100%; }
.b1 { background: var(--b1); } .b2 { background: var(--b2); }
.legend { font-size: .8rem; color: var(--muted); display: flex; gap: 14px; flex-wrap: wrap; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
footer { font-size: .8rem; color: var(--muted); }
.label { font-size: .75rem; color: var(--muted); letter-spacing: .03em; margin-top: 4px; }
pre { margin: 0; font-family: var(--mono); font-size: .8rem; background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
.chip:not(.ok):not(.re):not(.bad) { color: var(--muted); background: var(--bg); font-weight: 400; }
"""


def build(folder: Path) -> str:
    mm = folder / "mango_models_20261117.json"
    if mm.exists():
        MANGO_PRICES.update({m["id"]: m.get("cost") for m in json.loads(mm.read_text(encoding="utf-8"))["data"]})
    recs = merged(folder)
    ok = sum(r["ok"] for r in recs)
    first_ok = sum(r["ok"] for r in load(folder / "raw.jsonl"))
    retests = sum(r["retest"] for r in recs)
    cost = sum((r.get("generation") or {}).get("total_cost") or 0 for r in recs)
    sections = "".join(model_section(k, [r for r in recs if r["model_key"] == k]) for k in NAMES)
    return f"""<title>Mango·OpenRouter 간이 테스트</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{CSS}</style>
<div class="wrap">
  <header>
    <h1>Mango·OpenRouter 간이 테스트 결과</h1>
    <p class="lede">2026-11-17 · 본 실험 전에 4개 모델의 호출 경로 12개(Mango 직접 1, OpenRouter 11)와 도구 호출을 한 번씩 불러 동작과 기록 항목을 확인했습니다.</p>
    <p class="note">사용자 PC에서 경로마다 1회 호출한 값입니다. 네트워크 상태와 순간 부하가 그대로 섞여 있어 성능 비교에는 쓰지 않습니다.</p>
  </header>
  <div class="stats">
    <div class="stat"><b>{ok} / {len(recs)}</b><span>최종 성공 (재시험 {retests}건 포함)</span></div>
    <div class="stat"><b>{first_ok} / {len(recs)}</b><span>첫 실행 성공</span></div>
    <div class="stat"><b>${cost:.3f}</b><span>OpenRouter 비용 합계 (서버 기록)</span></div>
    <div class="stat"><b>4 / 4</b><span>Mango 모델 ID 확정</span></div>
  </div>
  <section>
    <h2>발견한 문제와 조치</h2>
    <div class="cards">
      <div class="card"><h3>Mango 8건 실패 <span class="chip re">402 잔액 부족</span></h3>
        <p>키와 주소(<code>api.mangoboost.io/v1</code>)는 정상이었고 모델 목록 조회도 됐습니다. 선불 잔액이 없어 호출만 막혔습니다.</p>
        <p class="fix">충전 후 재시험 8/8 성공 (텍스트 4, 도구 호출 4)</p></div>
      <div class="card"><h3>DeepSeek 고정 경로 실패 <span class="chip re">404 후보 없음</span></h3>
        <p>OpenRouter 계정의 "Paid model training" 차단 설정 때문에 DeepSeek 공식 엔드포인트가 후보에서 빠졌습니다. 기본 라우팅에서도 빠지므로 실험 B의 후보군이 달라지는 문제였습니다.</p>
        <p class="fix">설정을 허용으로 바꾼 뒤 재시험 성공 (응답 제공사 DeepSeek). 프로토콜 사전 조건에 추가</p></div>
    </div>
  </section>
  {prompts_section()}
  <section class="models">
    <h2 style="margin:0">모델별 경로 결과</h2>
    <div class="legend"><span><i style="background:var(--b1)"></i>첫 답변까지</span><span><i style="background:var(--b2)"></i>나머지 생성 시간</span><span>막대 길이는 모델 안에서 가장 긴 전체 시간 기준 · 재시험 표시에 마우스를 올리면 첫 실패 사유 · <sup>*</sup> Mango 비용은 응답에 없어 토큰 수 × Mango API 단가(할인 전 가격)로 계산한 값</span></div>
    {sections}
  </section>
  <section>
    <h2>요청별 데이터 확보 현황</h2>
    <div class="cards">
      <div class="card"><h3>확보됨</h3><ul>
        <li>성공 여부, 종료 이유, 오류 코드·메시지</li>
        <li>첫 토큰·첫 답변·전체 시간 (클라이언트 측정)</li>
        <li>입력·출력·reasoning·캐시 토큰 (Mango GLM-5.3은 reasoning을 <code>usage.reasoning_tokens</code>에 보고, 캐시 세부는 없음)</li>
        <li>OpenRouter: 실제 응답 제공사, 폴백 시도 내역, 서버 측 지연·생성 시간, 요청당 비용</li>
        <li>도구 호출 인자</li></ul></div>
      <div class="card"><h3>본 실험 코드에 추가할 것</h3><ul>
        <li>조각별 도착 시각 전체 (조각 간 간격 분포)</li>
        <li>요청 시각(UTC), nonce, 질문 ID</li>
        <li>연결 단계 시간(TCP·TLS), 호스트 IP·ASN, 응답 헤더</li>
        <li>OpenRouter Broadcast 구간 시간 (서울 VM 수신 주소 필요)</li>
        <li>Mango 요청당 비용: 응답에 없어 토큰 × 단가로 계산 후 콘솔 내역과 대조</li></ul></div>
    </div>
  </section>
  <footer>대조 확인: OpenRouter 대시보드의 요청 48건·$0.04와 Mango 대시보드의 처리 토큰 약 2.3K가 이 기록과 일치합니다. 원본은 저장소 <code>results/smoke_test_20261117/</code>에 있습니다.</footer>
</div>
"""


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    out = folder / "간이테스트_결과.html"
    out.write_text(build(folder), encoding="utf-8")
    print("written", out)
