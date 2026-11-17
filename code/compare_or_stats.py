"""1회 실험: OpenRouter 엔드포인트별 우리 실측 vs OpenRouter 기록(generation) vs 공개 성능 통계(30분 창 p50) 비교.
사용: python -I compare_or_stats.py <runs/run1 폴더 또는 results/run1_20261117>
"""
import json, sys, statistics as st, collections, math, os, gzip
D = sys.argv[1].rstrip('/') + '/'
def L(p):
    for c in (D + p, D + p + '.gz', D + os.path.basename(p), D + os.path.basename(p) + '.gz'):
        if os.path.exists(c):
            f = gzip.open(c, 'rt', encoding='utf-8') if c.endswith('.gz') else open(c, encoding='utf-8')
            return [json.loads(l) for l in f if l.strip()]
    raise FileNotFoundError(p)
gens = {g['request_id']: g for g in L('generations.jsonl') if 'generation_error' not in g}
raw = [r for r in L('raw.jsonl') if r.get('host') == 'or' and r.get('ok') and r.get('task') in ('W1', 'W2', 'W3')]
stats = L('or_stats/or_stats.jsonl')
pub = collections.defaultdict(list)  # (model, endpoint_id) -> snapshots
for s in stats:
    for e in s['endpoint_stats']:
        pub[(s['model_key'], e['endpoint_id'])].append(e)
ours = collections.defaultdict(lambda: collections.defaultdict(list))
names = {}
for r in raw:
    g = gens.get(r['request_id'])
    if not g or not g.get('attempts'): continue
    a = g['attempts'][-1]; k = (r['model_key'], a['endpoint_id']); names[k] = a.get('endpoint')
    o = ours[k]
    if r.get('ttft_any_ms'): o['ttft'].append(r['ttft_any_ms'])
    if r.get('decode_tps'): o['tps'].append(r['decode_tps'])
    if g.get('latency'): o['or_lat'].append(g['latency'])
    gt, lat, ct = g.get('generation_time'), g.get('latency'), g.get('native_tokens_completion')
    if gt and ct and gt > 0: o['or_tps_total'].append(ct / gt * 1000)
    if gt and lat and ct and gt - lat > 50: o['or_tps_decode'].append(ct / (gt - lat) * 1000)
def med(v): return st.median(v) if v else float('nan')
def spearman(x, y):
    def rk(v):
        s = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v)
        for j, i in enumerate(s): r[i] = j
        return r
    rx, ry = rk(x), rk(y); n = len(x)
    return 1 - 6 * sum((a - b) ** 2 for a, b in zip(rx, ry)) / (n * (n * n - 1)) if n > 2 else float('nan')
rows = []
for k, o in ours.items():
    if len(o['ttft']) < 10 or k not in pub: continue
    ps = pub[k]
    w = lambda f, c: med([e[f] for e in ps if e.get(f) is not None and (e.get(c) or 0) > 0])
    rows.append(dict(model=k[0], ep=names[k], n=len(o['ttft']),
        our_ttft=med(o['ttft']), or_lat=med(o['or_lat']), pub_lat=w('p50_latency', 'latency_request_count'),
        our_tps=med(o['tps']), or_tps_dec=med(o['or_tps_decode']), or_tps_tot=med(o['or_tps_total']),
        pub_tp=w('p50_throughput', 'throughput_request_count'), pub_speed=w('p50_speed', 'speed_request_count'),
        pub_n=med([e.get('request_count') or 0 for e in ps])))
rows.sort(key=lambda r: (r['model'], -r['n']))
print(f"{'model':14s} {'endpoint':26s} {'n':>4s} | TTFT ms: 우리 / OR기록 / 공개p50 | tok/s: 우리decode / OR기록decode / OR기록전체 / 공개throughput / 공개speed | 공개 30분 요청수")
for r in rows:
    print(f"{r['model'][:14]:14s} {str(r['ep'])[:26]:26s} {r['n']:4d} | {r['our_ttft']:6.0f} / {r['or_lat']:6.0f} / {r['pub_lat']:6.0f} | {r['our_tps']:5.0f} / {r['or_tps_dec']:5.0f} / {r['or_tps_tot']:5.0f} / {r['pub_tp']:5.0f} / {r['pub_speed']:5.0f} | {r['pub_n']:.0f}")
ok = lambda f: [r for r in rows if not any(math.isnan(r[x]) for x in f)]
def summ(a, b, label):
    rr = ok((a, b)); x = [r[a] for r in rr]; y = [r[b] for r in rr]
    rat = [r[a] / r[b] for r in rr if r[b]]
    print(f"{label}: 엔드포인트 {len(rr)}개, 비율 중앙값 {med(rat):.2f} (범위 {min(rat):.2f}~{max(rat):.2f}), 순위상관(Spearman) {spearman(x, y):.2f}")
print()
summ('our_ttft', 'or_lat', '우리 TTFT ÷ OR 기록 지연(같은 요청)')
summ('or_lat', 'pub_lat', 'OR 기록 지연 ÷ 공개 p50 지연')
summ('our_ttft', 'pub_lat', '우리 TTFT ÷ 공개 p50 지연')
summ('our_tps', 'or_tps_dec', '우리 decode ÷ OR 기록 decode(같은 요청)')
summ('our_tps', 'pub_tp', '우리 decode ÷ 공개 p50 throughput')
summ('or_tps_tot', 'pub_tp', 'OR 기록 전체 tok/s ÷ 공개 p50 throughput')
# per model rank agreement on decode (endpoint ordering)
print()
for m in sorted({r['model'] for r in rows}):
    rr = [r for r in ok(('our_tps', 'pub_tp', 'our_ttft', 'pub_lat')) if r['model'] == m]
    if len(rr) >= 4:
        print(f"{m}: 엔드포인트 {len(rr)}개 | decode 순위상관 우리 vs 공개 {spearman([r['our_tps'] for r in rr],[r['pub_tp'] for r in rr]):.2f} | TTFT 순위상관 {spearman([r['our_ttft'] for r in rr],[r['pub_lat'] for r in rr]):.2f}")
