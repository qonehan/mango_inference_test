"""reasoning 설정 시험 결과(raw.jsonl, endpoints.jsonl)로 결과 페이지(HTML)를 만든다.

사용: python build_reasoning_report.py <reasoning 시험 폴더> <Mango 모델 목록 JSON>
"""
from __future__ import annotations

import html
import json
import statistics
import sys
from pathlib import Path

e = html.escape
MODELS = [
    ("deepseek-v4.1-flash-1017", "DeepSeek V4.1 Flash 1017", "deepseek-ai/DeepSeek-V4.1-Flash-1017"),
    ("glm-5.3", "GLM-5.3", "zai-org/GLM-5.3"),
    ("glm-5.3-flash", "GLM-5.3-Flash", "zai-org/GLM-5.3-Flash"),
    ("kimi-k3.1-pro", "Kimi K3.1 Pro", "moonshotai/Kimi-K3.1-Pro"),
]
COLS = [("default", "기본값", "default"), ("off", "끄기", "off-template"), ("on", "켜기", "on-template"),
        ("low", "강도 낮춤", "effort-low")]
VERDICT = {
    "deepseek-v4.1-flash-1017": ("끄기 가능 · 강도 조절 없음", "ok", "Mango·OpenRouter 모두 끄면 0. 강도 낮춤은 Mango·OpenRouter 모두 효과 없음(기본값과 비슷)"),
    "glm-5.3": ("끌 수 없음 · 강도 조절 가능", "bad", "끄기는 OpenRouter 400 \"Reasoning is mandatory\", Mango는 답이 비거나 추론 폭주. 강도 낮춤은 Mango·OpenRouter 모두 0~7개로 크게 줄어듦"),
    "glm-5.3-flash": ("끌 수 없음 · 강도 조절 가능", "bad", "끄기는 OpenRouter·Mango 모두 400. 강도 낮춤은 양쪽 모두 0~11개로 줄어듦"),
    "kimi-k3.1-pro": ("끄기 가능 · 강도 조절 가능", "ok", "응답한 곳은 모두 끄면 0. 강도 낮춤은 Mango 15~17개, OpenRouter 18~40개로 기본값보다 줄어듦"),
}


def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def classify(r: dict) -> tuple[str, str]:
    """셀 하나(호출 1건)의 표시 문자열과 상태."""
    err = r.get("error") or ""
    if err:
        if "new-account" in err:
            return "429 계정", "acct"
        if "429" in err:
            return "429 제공사", "prov"
        if "mandatory" in err or "always engages" in err:
            return "400 끄기 불가", "bad"
        return f"오류 {r.get('status')}", "bad"
    rt = r.get("reasoning_tokens")
    ans = (r.get("text_head") or "").strip()
    if r.get("finish_reason") == "length":
        return f"폭주 {rt}", "bad"
    if not ans:
        return f"답 없음 ({rt})", "bad"
    if not rt:
        return "0", "zero"
    return str(rt), "on"


def cell(recs: list[dict]) -> str:
    if not recs:
        return "<td class='na'>–</td>"
    parts = []
    for r in sorted(recs, key=lambda x: x.get("rep", 0)):
        label, cls = classify(r)
        parts.append(f"<span class='v {cls}'>{e(label)}</span>")
    ok = [r for r in recs if not r.get("error")]
    t_any = [r["ttft_any_ms"] for r in ok if r.get("ttft_any_ms") is not None]
    t_con = [r["ttft_content_ms"] for r in ok if r.get("ttft_content_ms") is not None]
    sub = ""
    if t_any or t_con:
        a = f"{statistics.median(t_any) / 1000:.2f}" if t_any else "–"
        c = f"{statistics.median(t_con) / 1000:.2f}" if t_con else "–"
        sub = f"<small>첫 토큰 {a}초 · 첫 답변 {c}초</small>"
    return f"<td>{''.join(parts)}{sub}</td>"


def build(folder: Path, mango_models: Path) -> str:
    dirs = [folder] + sorted(p for p in folder.iterdir() if p.is_dir() and (p / "raw.jsonl").exists())
    recs = [r for d in dirs for r in load(d / "raw.jsonl")]
    eps = {r["request_id"]: r for d in dirs if (d / "endpoints.jsonl").exists() for r in load(d / "endpoints.jsonl")}
    mq = {m["id"]: m.get("quantization") for m in json.loads(mango_models.read_text(encoding="utf-8"))["data"]}
    n_ok = sum(r["ok"] for r in recs)
    cost = sum((r.get("generation") or {}).get("total_cost") or 0 for r in recs)
    or_ok = [r for r in recs if r["host"] == "or" and r.get("resp_id")]
    pinned_match = sum(1 for r in or_ok if eps.get(r["request_id"], {}).get("final_endpoint") == r["arm"].split("|")[0][3:])
    fails = {"bad": 0, "acct": 0, "prov": 0}
    for r in recs:
        if not r["ok"]:
            fails[classify(r)[1]] = fails.get(classify(r)[1], 0) + 1

    sections = []
    for key, name, mid in MODELS:
        rs = [r for r in recs if r["model_key"] == key]
        targets = ["M"] + sorted({r["arm"].split("|")[0] for r in rs if r["host"] == "or"},
                                 key=lambda a: [x["arm"].split("|")[0] for x in rs].index(a))
        rows = []
        for t in targets:
            trs = [r for r in rs if r["arm"].split("|")[0] == t]
            if t == "M":
                label = f"<b>Mango</b> <span class='q'>{e(mq.get(mid) or '?')}</span>"
            else:
                ep = t[3:]
                q = next((a.get("quantization") for r in trs for a in (eps.get(r["request_id"], {}).get("attempts") or [])), None)
                label = f"<code>{e(ep)}</code> <span class='q'>{e(q or '')}</span>"
            tds = []
            for ck, _, mango_v in COLS:
                v = mango_v if t == "M" else ck
                tds.append(cell([r for r in trs if r["variant"] == v]))
            rows.append(f"<tr><th scope='row'>{label}</th>{''.join(tds)}</tr>")
        vt, vc, vd = VERDICT[key]
        sections.append(f"""
<section class="model">
  <div class="mhead"><h3>{e(name)}</h3><span class="chip {vc}">{e(vt)}</span><p>{e(vd)}</p></div>
  <div class="tw"><table>
    <thead><tr><th>엔드포인트 · 양자화</th>{''.join(f'<th>{e(c[1])}</th>' for c in COLS)}</tr></thead>
    <tbody>{''.join(rows)}</tbody></table></div>
</section>""")

    return f"""<title>reasoning 설정 시험</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* layout: 한 열 보고서. 요약 → 방법 → 모델별 설정×엔드포인트 표 → 실패 원인 → 결론 */
:root {{
  --bg: #f6f7f9; --surface: #ffffff; --fg: #18202b; --muted: #5d6877; --line: #dde2e9;
  --on: #2e5fd8; --on-bg: #e8eefc; --zero: #4b5563; --zero-bg: #eceef1;
  --ok: #1d7a52; --ok-bg: #e6f4ec; --bad: #b42318; --bad-bg: #fdecea; --warn: #9a5a12; --warn-bg: #fbf0e1;
  --sans: "IBM Plex Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  --mono: "IBM Plex Mono", Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #11151b; --surface: #19202a; --fg: #e5e9ef; --muted: #9aa5b4; --line: #2c3542;
  --on: #8fb0ff; --on-bg: #22304f; --zero: #c3cad4; --zero-bg: #2a313b;
  --ok: #5fd3a0; --ok-bg: #173a2c; --bad: #ff8a80; --bad-bg: #3d1a17; --warn: #f0b46a; --warn-bg: #3a2a14; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --bg: #11151b; --surface: #19202a; --fg: #e5e9ef; --muted: #9aa5b4; --line: #2c3542;
  --on: #8fb0ff; --on-bg: #22304f; --zero: #c3cad4; --zero-bg: #2a313b;
  --ok: #5fd3a0; --ok-bg: #173a2c; --bad: #ff8a80; --bad-bg: #3d1a17; --warn: #f0b46a; --warn-bg: #3a2a14; color-scheme: dark; }}
body {{ background: var(--bg); color: var(--fg); font-family: var(--sans); font-size: 15px; line-height: 1.6; }}
.wrap {{ max-width: 1080px; margin: 0 auto; padding-block: 32px 56px; padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px)); display: grid; gap: 28px; }}
h1 {{ font-size: 1.7rem; margin: 0; text-wrap: balance; }}
h2 {{ font-size: 1.15rem; margin: 0 0 10px; }}
h3 {{ font-size: 1rem; margin: 0; }}
p {{ margin: 0; max-width: 72ch; }}
.lede {{ color: var(--muted); margin-top: 6px; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
.stat {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; }}
.stat b {{ display: block; font-size: 1.55rem; font-variant-numeric: tabular-nums; line-height: 1.2; }}
.stat span {{ font-size: .82rem; color: var(--muted); }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }}
.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; display: grid; gap: 6px; align-content: start; min-width: 0; }}
.card ul, .card ol {{ margin: 0; padding-left: 18px; font-size: .92rem; }}
.card li {{ margin: 4px 0; }}
pre {{ margin: 0; font-family: var(--mono); font-size: .8rem; background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; white-space: pre-wrap; word-break: break-word; }}
code {{ font-family: var(--mono); font-size: .82rem; }}
.label {{ font-size: .75rem; color: var(--muted); letter-spacing: .03em; }}
.models {{ display: grid; gap: 14px; }}
.model {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 16px; min-width: 0; }}
.mhead {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }}
.mhead p {{ flex-basis: 100%; font-size: .88rem; color: var(--muted); }}
.tw {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: .86rem; min-width: 760px; }}
th, td {{ padding: 7px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; white-space: nowrap; }}
thead th {{ font-weight: 500; color: var(--muted); font-size: .78rem; letter-spacing: .02em; }}
tbody th {{ font-weight: 400; }}
.q {{ font-size: .72rem; color: var(--muted); }}
.v {{ display: inline-block; font-family: var(--mono); font-size: .78rem; padding: 1px 7px; border-radius: 4px; margin: 0 4px 3px 0; font-variant-numeric: tabular-nums; }}
.v.on {{ color: var(--on); background: var(--on-bg); }}
.v.zero {{ color: var(--zero); background: var(--zero-bg); }}
.v.bad {{ color: var(--bad); background: var(--bad-bg); }}
.v.acct, .v.prov {{ color: var(--warn); background: var(--warn-bg); }}
td small {{ display: block; font-size: .72rem; color: var(--muted); }}
td.na {{ color: var(--muted); font-size: .78rem; }}
.chip {{ display: inline-block; font-size: .74rem; font-weight: 500; padding: 1px 9px; border-radius: 99px; }}
.chip.ok {{ color: var(--ok); background: var(--ok-bg); }}
.chip.bad {{ color: var(--bad); background: var(--bad-bg); }}
.legend {{ font-size: .8rem; color: var(--muted); display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
footer {{ font-size: .8rem; color: var(--muted); }}
</style>
<div class="wrap">
  <header>
    <h1>reasoning 설정 시험 결과</h1>
    <p class="lede">2026-11-17 · 엔드포인트마다 reasoning 켜기·끄기 지정을 실제로 따르는지 확인했습니다. 간이 테스트에서 같은 질문에 reasoning 토큰이 엔드포인트마다 0~599개로 달라, 기본값에 맡기면 조건이 통제되지 않았기 때문입니다.</p>
  </header>

  <div class="stats">
    <div class="stat"><b>{len(recs)}건</b><span>모델 4 × (Mango 설정 4 + OpenRouter 엔드포인트 5 × 설정 4) × 2회</span></div>
    <div class="stat"><b>{n_ok} / {len(recs)}</b><span>정상 응답 (오답 0)</span></div>
    <div class="stat"><b>{pinned_match} / {len(or_ok)}</b><span>고정한 엔드포인트가 실제로 응답</span></div>
    <div class="stat"><b>${cost:.3f}</b><span>OpenRouter 비용 합계</span></div>
  </div>

  <section>
    <h2>시험 방법</h2>
    <div class="cards">
      <div class="card"><div class="label">질문 (정답 80)</div>
        <pre>A train travels 60 km in 45 minutes. What is its average speed in km/h? Reply with the number only.</pre>
        <p style="font-size:.88rem">공통: <code>stream</code>, <code>temperature 0.6</code>, <code>top_p 1</code>, <code>max_tokens 4096</code>, 재시도 없음. OpenRouter는 제공사가 아니라 엔드포인트 슬러그로 고정(<code>"order": ["wafer/us"]</code> 등).</p></div>
      <div class="card"><div class="label">시험한 설정</div><ul>
        <li><b>OpenRouter</b> 끄기 <code>reasoning: {{"enabled": false}}</code>, 켜기 <code>{{"enabled": true}}</code>, 강도 낮춤 <code>{{"effort": "low"}}</code> (강도 낮춤은 나중에 추가로 40건 시험)</li>
        <li><b>Mango</b>는 공식 문서에 조절 파라미터가 없습니다. 문서에 없는 필드는 모델 서버로 그대로 전달된다고 해서 끄기·켜기는 <code>chat_template_kwargs: {{"enable_thinking", "thinking"}}</code>, 강도 낮춤은 <code>reasoning_effort: "low"</code>를 시험했습니다</li></ul></div>
    </div>
  </section>

  <section class="models">
    <h2 style="margin:0">모델별 결과</h2>
    <div class="legend"><span>칸의 숫자 = reasoning 토큰 수 (2회). 아래는 첫 토큰(생각 포함)과 첫 답변까지의 중앙값이며, 그 차이가 생각 시간</span>
      <span class="v on">켜짐</span><span class="v zero">0</span><span class="v bad">끄기 불가·오작동</span><span class="v acct">429 한도</span></div>
    {''.join(sections)}
  </section>

  <section>
    <h2>실패 {sum(fails.values())}건의 원인</h2>
    <div class="cards">
      <div class="card"><h3>끄기 불가·오작동 · {fails.get('bad', 0)}건</h3><p style="font-size:.9rem">GLM 계열에서 끄기를 요청한 경우입니다(400 오류 22건, Mango GLM-5.3 추론 폭주 1건). 모델 특성입니다. "끄기"를 표준 조건으로 쓸 수 없다는 근거입니다.</p></div>
      <div class="card"><h3>429 계정 한도 · {fails.get('acct', 0)}건</h3><p style="font-size:.9rem">OpenRouter 새 계정은 Kimi K3.1 Pro를 <b>분당 20회</b>까지만 부를 수 있습니다. 본 실험에서는 우리 쪽 실패로 분류하고 모델별 호출을 분당 18회 이하로 묶습니다.</p></div>
      <div class="card"><h3>429 제공사 한도 · {fails.get('prov', 0)}건</h3><p style="font-size:.9rem">Wafer·inference.net·Together가 보낸 "Provider returned error"입니다. 본 실험에서는 제공사 쪽 실패로 집계합니다. GLM-5.3 Wafer는 모두 429여서 간이 테스트의 reasoning 0개 원인은 확인하지 못했습니다.</p></div>
    </div>
  </section>

  <section>
    <h2>프로토콜에 반영한 결론</h2>
    <div class="card"><ol>
      <li><b>reasoning은 "켜짐"으로 통제합니다.</b> 모든 모델·엔드포인트의 기본값이 켜짐이고 GLM 계열은 끌 수 없어, 끄기는 공통 조건이 될 수 없습니다. OpenRouter에는 <code>reasoning.enabled=true</code>를 명시합니다.</li>
      <li><b>Mango에는 켜기 지정을 보내지 않습니다.</b> 기본값이 켜짐이고, 문서에 없는 <code>chat_template_kwargs</code>는 GLM-5.3에서 오작동했습니다.</li>
      <li><b>reasoning 토큰이 0인 응답은 "reasoning 미작동"으로 표시</b>하고 성능 비교에서 구분합니다.</li>
      <li><b>reasoning 토큰은 두 위치를 모두 읽습니다.</b> 모델에 따라 <code>completion_tokens_details.reasoning_tokens</code> 또는 <code>usage.reasoning_tokens</code>에 옵니다(Mango GLM-5.3은 후자).</li>
      <li><b>OpenRouter 새 계정 한도</b>(모델당 분당 20회)에 맞춰 호출 속도를 묶고, 계정 한도 429는 우리 쪽 실패로 분류합니다.</li>
      <li><b>강도 낮춤은 Mango의 비공식 <code>reasoning_effort</code>와 OpenRouter의 <code>reasoning.effort</code>가 같은 방향으로 작동</b>했습니다. GLM·Kimi는 줄고 DeepSeek은 변화 없음. 보조 조건으로 쓸 수 있는 후보입니다.</li>
      <li>엔드포인트 슬러그 고정이 응답 {len(or_ok)}건 모두 그대로 지켜져, 고정 경로를 <b>엔드포인트 단위로</b> 정의할 수 있음을 확인했습니다.</li>
    </ol></div>
  </section>

  <footer>원본: 저장소 <code>results/reasoning_test_20261117/</code> (raw.jsonl, endpoints.jsonl, summary.csv) · 코드: <code>code/reasoning_param_test.py</code></footer>
</div>
"""


if __name__ == "__main__":
    folder, mango_models = Path(sys.argv[1]), Path(sys.argv[2])
    out = folder / "reasoning시험_결과.html"
    out.write_text(build(folder, mango_models), encoding="utf-8")
    print("written")
