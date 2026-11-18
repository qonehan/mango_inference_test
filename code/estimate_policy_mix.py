"""1회 실험: OpenRouter 전체 트래픽 중 정책별(기본 라우팅·가격 우선·처리량 우선·지연 우선·직접 지정) 비중의 범위 추정.

입력: 실행 중 30분마다 저장한 공개 성능 페이지의 엔드포인트별 request_count(8회 합 = 실행 4시간의 전체 사용자 요청),
      우리가 측정한 정책별 첫 시도 엔드포인트 분포(generation attempts[0]).
모형: 공개 요청 비중 s(e) = a_d*d(e) + a_p*p(e) + a_t*t(e) + a_l*l(e) + x(e),  x(e) >= 0 (직접 지정·BYOK 등 나머지)
  d = OR 기본 (W1·W3·W2 1턴·L·Z·부하, session_id 고착이 없는 첫 요청만)
  p = 가격 우선 (sort:price + :floor), t = 처리량 우선 (sort:throughput + :nitro), l = 지연 우선 (sort:latency)
선형계획: (1) 각 자동 정책 비중의 상한 = 그 정책 하나만 최대화, (2) 직접 지정의 하한 = 1 - max(a_d+a_p+a_t+a_l),
  (3) 직접 지정을 최소로 할 때 기본 라우팅 비중의 범위.
불확실성: 정책 분포를 첫 시도 표본에서 2,000회 다항 재표본(시드 20261116), 2.5~97.5% 백분위.
보수 값: 각 엔드포인트 배분 비율 대신 Wilson 95% 하한을 써서 제약을 느슨하게 한 값(자동 정책 상한이 커지고 직접 지정 하한이 작아짐).
사용: python code/estimate_policy_mix.py results/run1_20261117 results/run1_20261117/analysis/policy_mix.json
공개 기록의 .jsonl.gz 파일을 그대로 읽습니다(비공개 원본은 압축하지 않은 .jsonl, or_stats/or_stats.jsonl).
"""
import json, sys, collections, math, random, gzip, os
import numpy as np
from scipy.optimize import linprog
D = sys.argv[1].rstrip('/') + '/'
OUT = sys.argv[2]


def op(name):
    """name 또는 name.gz, 공개 기록의 평평한 경로(or_stats.jsonl.gz)를 순서대로 찾는다."""
    for p in (D + name, D + name + '.gz', D + os.path.basename(name), D + os.path.basename(name) + '.gz'):
        if os.path.exists(p):
            return gzip.open(p, 'rt', encoding='utf-8') if p.endswith('.gz') else open(p, encoding='utf-8')
    raise FileNotFoundError(D + name)

NAMES = {'deepseek-v4.1-flash-1017': 'DeepSeek V4.1 Flash', 'glm-5.3': 'GLM-5.3', 'glm-5.3-flash': 'GLM-5.3 Flash', 'kimi-k3.1-pro': 'Kimi K3.1 Pro'}
g = {x['request_id']: x for x in map(json.loads, op('generations.jsonl')) if 'generation_error' not in x}
pub = collections.defaultdict(collections.Counter)
for l in op('or_stats/or_stats.jsonl'):
    s = json.loads(l)
    for e in s['endpoint_stats']:
        pub[s['model_key']][e['endpoint_id']] += e.get('request_count') or 0
GROUP = {'OR-default': 'd', 'OR-sort:price': 'p', 'OR-floor': 'p', 'OR-sort:throughput': 't', 'OR-nitro': 't', 'OR-sort:latency': 'l'}
obs = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
for l in op('raw.jsonl'):
    r = json.loads(l)
    if r.get('host') != 'or' or r.get('task') == 'WARMUP' or r.get('arm') not in GROUP: continue
    if r['arm'] == 'OR-default' and r['task'] == 'W2' and (r.get('turn') or 1) > 1: continue
    x = g.get(r['request_id'])
    if x and x.get('attempts'): obs[r['model_key']][GROUP[r['arm']]][x['attempts'][0]['endpoint_id']] += 1
POL = ['d', 'p', 't', 'l']
def wilson_lo(k, n, z=1.96):
    if k == 0: return 0.0
    ph = k / n; den = 1 + z * z / n
    return (ph + z * z / (2 * n) - z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))) / den
def solve(E, s, comp):
    A = np.array([[comp[k].get(e, 0.0) for k in POL] for e in E]); b = s
    res = {}
    for i, k in enumerate(POL):
        c = np.zeros(4); c[i] = -1
        res['max_' + k] = -linprog(c, A_ub=A, b_ub=b, bounds=[(0, None)] * 4, method='highs').fun
    tot = -linprog(-np.ones(4), A_ub=A, b_ub=b, bounds=[(0, None)] * 4, method='highs').fun
    res['explicit_min'] = 1 - tot
    Aeq = np.ones((1, 4)); beq = [tot - 1e-9]
    lo = linprog(np.array([1, 0, 0, 0]), A_ub=np.vstack([A, -Aeq]), b_ub=np.concatenate([b, [-beq[0]]]), bounds=[(0, None)] * 4, method='highs')
    hi = linprog(np.array([-1, 0, 0, 0]), A_ub=np.vstack([A, -Aeq]), b_ub=np.concatenate([b, [-beq[0]]]), bounds=[(0, None)] * 4, method='highs')
    res['d_at_min_explicit_lo'] = lo.x[0]; res['d_at_min_explicit_hi'] = hi.x[0]
    return res
rng = random.Random(20261116)
out = []
for mk in NAMES:
    P = pub[mk]; T = sum(P.values()); E = list(P); s = np.array([P[e] / T for e in E])
    O = obs[mk]; n = {k: sum(O[k].values()) for k in POL}
    plug = solve(E, s, {k: {e: c / n[k] for e, c in O[k].items()} for k in POL})
    cons = solve(E, s, {k: {e: wilson_lo(c, n[k]) for e, c in O[k].items()} for k in POL})
    boots = []
    for _ in range(2000):
        comp = {}
        for k in POL:
            keys = list(O[k]); w = [O[k][e] for e in keys]
            draw = collections.Counter(rng.choices(keys, weights=w, k=n[k]))
            comp[k] = {e: c / n[k] for e, c in draw.items()}
        boots.append(solve(E, s, comp))
    def ci(key):
        v = sorted(b[key] for b in boots); return [round(v[49], 4), round(v[1949], 4)]
    reach = set().union(*[set(O[k]) for k in POL])
    row = {'model': NAMES[mk], 'public_requests_4h': T, 'n_obs': n,
           'unreached_share': round(sum(P[e] for e in E if e not in reach) / T, 4)}
    for key in plug:
        row[key] = round(plug[key], 4); row[key + '_ci'] = ci(key); row[key + '_conservative'] = round(cons[key], 4)
    out.append(row)
    print(NAMES[mk], {k: (row[k], row[k + '_ci'], row[k + '_conservative']) for k in plug}, 'unreached', row['unreached_share'])
json.dump({'rows': out, 'meta': [{'label': 'estimate_policy_mix.py', 'value': open(__file__, encoding='utf-8').read(), 'type': 'code', 'lang': 'python'},
           {'label': 'Input', 'value': 'or_stats/or_stats.jsonl(request_count 8회 합) + raw.jsonl + generations.jsonl(attempts[0])'}]},
          open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
