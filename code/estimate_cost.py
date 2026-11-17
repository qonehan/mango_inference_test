"""설정 파일의 건수에 파일럿 단가(경로·작업·모델별 평균 청구액)를 곱해 예상 비용을 낸다.

사용: python code/estimate_cost.py code/config/run1.yaml results/pilot_20261117
단가: Mango는 정가 × usage, OpenRouter는 generation 청구액. 파일럿 실패 요청 비용 포함.
"""
import collections, gzip, json, os, sys
from bench_common import load_config

cfg = load_config(sys.argv[1])
D = sys.argv[2] if len(sys.argv) > 2 else "results/pilot_20261117"
op = lambda n: gzip.open(os.path.join(D, n + ".gz"), "rt") if os.path.exists(os.path.join(D, n + ".gz")) else open(os.path.join(D, n))
rows = [json.loads(l) for l in op("raw.jsonl")]
gens = {g["resp_id"]: g for g in map(json.loads, op("generations.jsonl"))}
mm = {m["id"]: m for m in json.load(open(os.path.join(D, "mango_models.json")))["data"]}
grp = lambda a: "M" if a == "M" else "OR-default" if a.startswith("OR-default") else "pin" if "pin" in a else "sort"
U = collections.defaultdict(list)
for r in rows:
    if r["task"] == "WARMUP" or (r["task"] == "W2" and r.get("prompt_tokens") is None and r.get("status_note") == "session_stopped"):
        continue
    if r["arm"] == "M":
        if r.get("prompt_tokens") is None:
            c = 0.0
        else:
            p = mm[r["model"]]["cost"]; ca = r.get("cached_tokens") or 0
            c = ((r["prompt_tokens"] - ca) * p["input"] + ca * p["cache"]["read"] + (r.get("completion_tokens") or 0) * p["output"]) / 1e6
    else:
        c = (gens.get(r.get("resp_id")) or {}).get("total_cost") or 0.0
    U[(r["model_key"], grp(r["arm"]), r["task"])].append(c)
unit = lambda mk, g, t: (sum(U[(mk, g, t)]) / len(U[(mk, g, t)])) if U[(mk, g, t)] else 0.0
C = cfg["counts"]; n_sort = len(cfg["sort_routes"]) + 2
tot = collections.Counter()
for mk, m in cfg["models"].items():
    plan = {
        ("M", "W1"): C["common_w1"], ("M", "W3"): C["common_w3"], ("M", "W2"): 10 * C["w2_sessions_per_slot"],
        ("OR-default", "W1"): C["or_default_w1"], ("OR-default", "W3"): C["or_default_w3"],
        ("OR-default", "W2"): 10 * (C["w2_sessions_per_slot"] + C.get("w2_nosid_sessions", 0)),
        ("M", "L"): 3 * C["l_per_length"], ("OR-default", "L"): 3 * C["l_per_length"],
        ("M", "Z"): C["z"], ("OR-default", "Z"): C["z"], ("M", "CACHE"): 2 * C["cache_pairs"],
        ("M", "LOAD"): C["load"], ("OR-default", "LOAD"): C["load"],
        ("pin", "W1"): len(m["pins"]) * C.get("pin_w1", C["common_w1"]), ("pin", "W3"): len(m["pins"]) * C.get("pin_w3", C["common_w3"]),
        ("pin", "W2"): len(m["pins"]) * 10 * C["pin_w2_sessions"],
        ("sort", "W1"): n_sort * C["sort_w1"], ("sort", "W3"): n_sort * C["sort_w3"],
    }
    if C.get("pin_extras", True):
        plan.update({("pin", "L"): len(m["pins"]) * 3 * C["l_per_length"], ("pin", "Z"): len(m["pins"]) * C["z"],
                     ("pin", "CACHE"): len(m["pins"]) * 2 * C["cache_pairs"]})
    for (g, t), n in plan.items():
        host = "mango" if g == "M" else "openrouter"
        tot[(host, "본 루프")] += n * unit(mk, g, t)
        tot[("calls", "본 루프")] += n
# 품질 점검 Q: 파일럿 Mango 문항당 비용을 M에, OR-기본은 같은 값으로 근사(OR Q 청구액은 파일럿에서 미집계)
qpm = {"deepseek-v4.1-flash-1017": 1.04e-3, "glm-5.3": 10.53e-3, "glm-5.3-flash": 1.33e-3, "kimi-k3.1-pro": 17.53e-3}
nq = cfg.get("q_items_per_model", 0)
for mk in cfg["models"]:
    tot[("mango", "Q")] += nq * qpm[mk]; tot[("openrouter", "Q")] += nq * qpm[mk]; tot[("calls", "Q")] += 2 * nq
for k in sorted(tot):
    print(f"{k[0]:10s} {k[1]:6s} {tot[k]:10.2f}")
m = tot[("mango", "본 루프")] + tot[("mango", "Q")]; o = tot[("openrouter", "본 루프")] + tot[("openrouter", "Q")]
print(f"합계: Mango ${m:.2f} + OpenRouter ${o:.2f} = ${m + o:.2f} | 호출 {int(tot[('calls', '본 루프')] + tot[('calls', 'Q')])}건")
