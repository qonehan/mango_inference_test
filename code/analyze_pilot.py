"""파일럿(2026-11-17) 심층 분석. 주 분석기(analyze.py)의 P1·P2·P4 값이 왜 그렇게 나왔는지 분해한다.

사용법:
    python code/analyze_pilot.py results/pilot_20261117 > results/pilot_20261117/analysis/deep_analysis.txt

입력 폴더에는 raw.jsonl(.gz), generations.jsonl(.gz), mango_models.json, q_graded.jsonl이 있어야 한다.
모든 값은 파일럿(1회차, 세션 6개·질문 5개) 기술 통계이며 판정에 쓰지 않는다.
"""
import collections
import gzip
import json
import math
import os
import statistics as st
import sys

D = sys.argv[1] if len(sys.argv) > 1 else "results/pilot_20261117"


def load(name):
    p = os.path.join(D, name)
    op = gzip.open if os.path.exists(p + ".gz") else open
    with op(p + ".gz" if os.path.exists(p + ".gz") else p, "rt", encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


rows = [r for r in load("raw.jsonl") if r["task"] != "WARMUP"]
gens = {g["resp_id"]: g for g in load("generations.jsonl")}
mm = {m["id"]: m for m in json.load(open(os.path.join(D, "mango_models.json"), encoding="utf-8"))["data"]}
q = load("q_graded.jsonl") if os.path.exists(os.path.join(D, "q_graded.jsonl")) else load(os.path.join("q", "q_graded.jsonl"))
MODELS = sorted({r["model_key"] for r in rows})
MID = {mk: next(r["model"] for r in rows if r["model_key"] == mk and r["arm"] == "M" and "model" in r) for mk in MODELS}


def gm(v):
    v = [x for x in v if x and x > 0]
    return math.exp(sum(map(math.log, v)) / len(v)) if v else float("nan")


def med(v):
    return st.median(v) if v else float("nan")


def qt(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))] if v else float("nan")


def mango_cost(r, c):
    ca = r.get("cached_tokens") or 0
    return ((r["prompt_tokens"] - ca) * c["input"] + ca * c["cache"]["read"] + (r.get("completion_tokens") or 0) * c["output"]) / 1e6


def w2_sessions(mk):
    s = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        if r["model_key"] == mk and r["task"] == "W2" and r["arm"] in ("M", "OR-default"):
            s[r["block_id"]][r["arm"]].append(r)
    return s


print("## 1. 실패 재분류 (의도된 length 종료 분리)")
c = collections.Counter()
for r in rows:
    if r["ok"]:
        continue
    if r["task"] in ("Z", "L") and r.get("finish_reason") == "length" and (r.get("completion_tokens") or 0) <= 64:
        c["의도된 종료(Z·L 출력 상한)"] += 1
    elif r["task"] == "W2" and r.get("error") is None:
        c["W2 세션 중단으로 미송신"] += 1
    elif (r.get("completion_tokens") or 0) >= 32768:
        c["추론 폭주(32,768 상한 도달)"] += 1
    elif r.get("error") == "deadline":
        c["300초 마감"] += 1
    else:
        c["기타 " + str((r["task"], r.get("error"), r.get("finish_reason")))] += 1
for k, v in c.most_common():
    print(f"  {v:4d} {k}")

print("\n## 2. 추론 폭주(32,768 상한 또는 300초 마감) 비율, W1·W2·W3·LOAD 송신 요청 기준")
for mk in MODELS:
    for side in ("M", "OR"):
        s = [r for r in rows if r["model_key"] == mk and r["task"] in ("W1", "W2", "W3", "LOAD")
             and (r["arm"] == "M") == (side == "M") and not (r["task"] == "W2" and r.get("error") is None and not r["ok"])]
        run = [r for r in s if (r.get("completion_tokens") or 0) >= 32768 or r.get("error") == "deadline"]
        print(f"  {mk:26s} {side:2s} 송신 {len(s):4d} 폭주 {len(run):2d} ({100 * len(run) / len(s):.1f}%)")

print("\n## 3. W2 TTFT 분해 (세션별 2~10턴 기하평균의 M÷OR-기본, 양쪽 성공 턴만)")
print("   TTFT any = 첫 토큰(추론 포함), 추론 구간 = content − any")
for mk in MODELS:
    ra, rc, rph, rr = [], [], [], []
    for b, a in w2_sessions(mk).items():
        if len(a) < 2:
            continue
        t = {k: {x["turn"]: x for x in v if x["ok"] and x["turn"] >= 2} for k, v in a.items()}
        com = sorted(set(t["M"]) & set(t["OR-default"]))
        if len(com) < 3:
            continue
        f = lambda k, key: gm([t[k][i][key] for i in com])
        ra.append(f("M", "ttft_any_ms") / f("OR-default", "ttft_any_ms"))
        rc.append(f("M", "ttft_content_ms") / f("OR-default", "ttft_content_ms"))
        ph = lambda k: med([t[k][i]["ttft_content_ms"] - t[k][i]["ttft_any_ms"] for i in com])
        pm, po = ph("M"), ph("OR-default")
        if pm > 0 and po > 0:  # 추론 구간이 0인 세션(추론 없이 바로 답변)은 비율에서 제외 (1회 실험에서 0 나누기 발생, 2026-11-18 수정)
            rph.append(pm / po)
        rr.append(med([t["M"][i]["reasoning_tokens"] or 0 for i in com]) / max(1, med([t["OR-default"][i]["reasoning_tokens"] or 0 for i in com])))
    print(f"  {mk:26s} 세션 {len(ra)} | TTFT any {gm(ra):.2f} | TTFT content {gm(rc):.2f} | 추론 구간 시간 {gm(rph):.2f} (세션 {len(rph)}) | 추론 토큰 {gm(rr):.2f}")

print("\n## 4. 관측 오버헤드: Z(출력 1토큰) TTFT any 중앙값")
for mk in MODELS:
    o = []
    for a in ("M", "OR-default"):
        v = [r["ttft_any_ms"] for r in rows if r["model_key"] == mk and r["arm"] == a and r["task"] == "Z" and r.get("ttft_any_ms")]
        o.append(f"{a} {med(v):.0f}ms (n{len(v)})")
    print(f"  {mk:26s} " + " | ".join(o))

print("\n## 5. decode 처리량 중앙값 순위 (W1+W3+W2 성공, 12경로)")
for mk in MODELS:
    d = collections.defaultdict(list)
    for r in rows:
        if r["model_key"] == mk and r["ok"] and r["task"] in ("W1", "W3", "W2") and r.get("decode_tps"):
            d[r["arm"]].append(r["decode_tps"])
    rk = sorted(d, key=lambda a: -med(d[a]))
    print(f"  {mk:26s} M {med(d['M']):.0f} tok/s, 순위 {rk.index('M') + 1}/{len(rk)} | 상위: " + ", ".join(f"{a} {med(d[a]):.0f}" for a in rk[:3]) + f" | OR-default {med(d['OR-default']):.0f}")

print("\n## 6. W2 2~10턴 TTFT content 꼬리 (ms)")
for mk in MODELS:
    o = []
    for a in ("M", "OR-default"):
        v = [r["ttft_content_ms"] for r in rows if r["model_key"] == mk and r["arm"] == a and r["task"] == "W2" and r["ok"] and r["turn"] >= 2]
        o.append(f"{a} p50 {qt(v, .5):.0f} p90 {qt(v, .9):.0f} (p90/p50 {qt(v, .9) / qt(v, .5):.1f})")
    print(f"  {mk:26s} " + " | ".join(o))

print("\n## 7. OR-기본의 W2 제공사 고착과 턴별 캐시 적중률")
pat = collections.Counter()
hit = collections.defaultdict(list)
for mk in MODELS:
    for b, a in w2_sessions(mk).items():
        for arm, v in a.items():
            t = {x["turn"]: x for x in v if x.get("prompt_tokens")}
            for i, x in t.items():
                hit[(arm, i)].append(100 * (x.get("cached_tokens") or 0) / x["prompt_tokens"])
            if arm == "OR-default":
                pv = {i: gens[x["resp_id"]]["provider_name"] for i, x in t.items() if x.get("resp_id") in gens}
                pat["1턴과 2턴 제공사 다름" if pv.get(1) != pv.get(2) else "1턴과 2턴 제공사 같음"] += 1
                pat["2~10턴 한 제공사" if len({pv[i] for i in pv if i >= 2}) == 1 else "2턴 이후 제공사 바뀜"] += 1
print("  OR-기본 세션:", dict(pat))
for arm in ("M", "OR-default"):
    print(f"  {arm:10s} 턴별 평균 캐시 적중률(%): " + " ".join(f"{i}:{st.mean(hit[(arm, i)]):.0f}" for i in range(1, 11) if hit[(arm, i)]))

print("\n## 8. P4(W2 성공 세션당 비용) 분해: 비율 = 단가 효과 × 토큰량 효과 × 성공 세션 수 효과")
print("   단가 효과 = M 토큰을 Mango 정가로 ÷ M 토큰을 OR-기본 실효 단가로. 토큰량 효과 = M 토큰 ÷ OR 토큰(둘 다 OR 실효 단가)")
for mk in MODELS:
    c = mm[MID[mk]]["cost"]
    S = w2_sessions(mk)
    tok = collections.defaultdict(collections.Counter)
    pu = pc = po = 0.0
    for b, a in S.items():
        for arm, v in a.items():
            for r in v:
                if r.get("prompt_tokens") is None:
                    continue
                ca = r.get("cached_tokens") or 0
                tok[arm]["u"] += r["prompt_tokens"] - ca
                tok[arm]["c"] += ca
                tok[arm]["o"] += r.get("completion_tokens") or 0
                if arm == "M":
                    tok[arm]["usd"] += mango_cost(r, c)
                else:
                    g = gens[r["resp_id"]]
                    tok[arm]["usd"] += g["total_cost"] or 0
                    at = g["attempts"][-1]
                    pu += (r["prompt_tokens"] - ca) * float(at["price_in"])
                    pc += ca * float(at.get("price_cache_read") or 0)
                    po += (r.get("completion_tokens") or 0) * float(at["price_out"])
    m, o = tok["M"], tok["OR-default"]
    eu, ec, eo = pu / o["u"], pc / o["c"], po / o["o"]
    m_at_or = m["u"] * eu + m["c"] * ec + m["o"] * eo
    succ = {arm: sum(1 for a in S.values() if a[arm] and all(x["ok"] for x in a[arm])) for arm in ("M", "OR-default")}
    ratio = (m["usd"] / succ["M"]) / (o["usd"] / succ["OR-default"])
    print(f"  {mk:26s} 비율 {ratio:.2f} = 단가 {m['usd'] / m_at_or:.2f} × 토큰량 {m_at_or / (pu + pc + po):.2f} × 성공 수 {succ['OR-default'] / succ['M']:.2f}"
          f" | 비캐시 입력 M {m['u']:,} OR {o['u']:,} · 출력 M {m['o']:,} OR {o['o']:,}"
          f" | 단가($/M 입력·캐시·출력) Mango {c['input']}/{c['cache']['read']}/{c['output']} OR실효 {eu * 1e6:.3f}/{ec * 1e6:.3f}/{eo * 1e6:.3f}")

print("\n## 9. 품질 점검 Q (경로당 20문항, 정답 수)")
acc = collections.defaultdict(lambda: [0, 0])
for r in q:
    acc[(r["model_key"], r["arm"])][0] += 1
    acc[(r["model_key"], r["arm"])][1] += r["correct"] or 0
for mk in MODELS:
    P = collections.defaultdict(dict)
    for r in q:
        if r["model_key"] == mk:
            P[r["item_id"]][r["arm"]] = r["correct"] or 0
    b = sum(1 for p in P.values() if p.get("M") == 1 and p.get("OR-default") == 0)
    cc = sum(1 for p in P.values() if p.get("M") == 0 and p.get("OR-default") == 1)
    orr = [v for (m_, a), v in acc.items() if m_ == mk and a != "M"]
    print(f"  {mk:26s} M {acc[(mk, 'M')][1]}/{acc[(mk, 'M')][0]} | OR-default {acc[(mk, 'OR-default')][1]}/{acc[(mk, 'OR-default')][0]}"
          f" | OR 6경로 합 {sum(x[1] for x in orr)}/{sum(x[0] for x in orr)} | M만 정답 {b}, OR-기본만 정답 {cc}")

print("\n## 10. Mango 파일럿 본 루프 비용(정가 적용, 실패 요청 포함)")
tot = fail = 0.0
for r in rows + [r for r in load("raw.jsonl") if r["task"] == "WARMUP"]:
    if r["arm"] != "M" or r.get("prompt_tokens") is None:
        continue
    x = mango_cost(r, mm[r["model"]]["cost"])
    tot += x
    fail += 0 if r["ok"] else x
print(f"  본 루프 ${tot:.4f}, 그중 실패 요청 ${fail:.4f}. OR 본 루프 generation 합계 ${sum((g.get('total_cost') or 0) for g in gens.values()):.4f}")
