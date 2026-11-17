"""1회 실험 탐색 항목 집계: 입력 길이(L), 동시 4건 부하(LOAD) vs W1, Mango 캐시 대조, OR-고정 제공사 vs M, W3 도구 호출.
사용: python -I explore_run1.py <runs/run1 폴더 또는 raw.jsonl(.gz)이 있는 폴더>
"""
import json, collections, statistics as st, sys, os, gzip
D = sys.argv[1].rstrip('/') + '/'
p = D + 'raw.jsonl'
f = gzip.open(p + '.gz', 'rt', encoding='utf-8') if not os.path.exists(p) else open(p, encoding='utf-8')
raw = [json.loads(l) for l in f if l.strip()]
m = [r for r in raw if r.get('task') != 'WARMUP' and r.get('host')]
med = lambda v: st.median(v) if v else float('nan')
MK = ['deepseek-v4.1-flash-1017', 'glm-5.3', 'glm-5.3-flash', 'kimi-k3.1-pro']
print('## 1. L (입력 길이) TTFT any ms, 모델·경로·길이별 1건')
for mk in MK:
    out = []
    for arm in ('M', 'OR-default'):
        rs = sorted([r for r in m if r['model_key'] == mk and r['arm'] == arm and r['task'] == 'L'], key=lambda r: r.get('prompt_tokens') or 0)
        out.append(arm + ': ' + ', '.join(f"{(r.get('prompt_tokens') or 0)//1000}k→{r.get('ttft_any_ms') or 0:.0f}" for r in rs))
    print(' ', mk, ' | '.join(out))
print('\n## 2. LOAD(동시 4, 8건) vs W1(동시 1, 성공) 중앙값: TTFT any ms, decode tok/s, 실패')
for mk in MK:
    s = []
    for arm in ('M', 'OR-default'):
        ld = [r for r in m if r['model_key'] == mk and r['arm'] == arm and r['task'] == 'LOAD']
        w1 = [r for r in m if r['model_key'] == mk and r['arm'] == arm and r['task'] == 'W1' and r['ok']]
        s.append(f"{arm}: TTFT {med([r['ttft_any_ms'] for r in ld if r.get('ttft_any_ms')]):.0f} vs {med([r['ttft_any_ms'] for r in w1 if r.get('ttft_any_ms')]):.0f}, "
                 f"decode {med([r['decode_tps'] for r in ld if r.get('decode_tps')]):.0f} vs {med([r['decode_tps'] for r in w1 if r.get('decode_tps')]):.0f}, 실패 {sum(not r['ok'] for r in ld)}/{len(ld)}")
    print(' ', mk, ' | '.join(s))
print('\n## 3. 캐시 대조 (M, 같은 입력 2회): prompt, cached, TTFT any ms')
for r in [r for r in m if r['task'] == 'CACHE']:
    print(' ', r['model_key'], r.get('item_id'), r.get('prompt_tokens'), r.get('cached_tokens'), round(r.get('ttft_any_ms') or 0))
print('\n## 4. OR-고정 제공사 vs M (W1+W3 성공, 고정 경로당 6건): decode 중앙값, TTFT any 중앙값')
for mk in MK:
    d = collections.defaultdict(list); t = collections.defaultdict(list)
    for r in m:
        if r['model_key'] == mk and r['ok'] and r['task'] in ('W1', 'W3') and (r['arm'] == 'M' or r['arm'].startswith('OR-pin')):
            if r.get('decode_tps'): d[r['arm']].append(r['decode_tps'])
            if r.get('ttft_any_ms'): t[r['arm']].append(r['ttft_any_ms'])
    print(' ', mk, ' | '.join(f"{a.replace('OR-pin:', '')} {med(d[a]):.0f} tok/s {med(t[a]):.0f}ms" for a in sorted(d, key=lambda a: -med(d[a]))))
print('\n## 5. W3 도구 호출: 성공/전체, 유효 도구 호출')
for mk in MK:
    s = []
    for arm in ('M', 'OR-default'):
        w3 = [r for r in m if r['model_key'] == mk and r['arm'] == arm and r['task'] == 'W3']
        s.append(f"{arm} {sum(r['ok'] for r in w3)}/{len(w3)}, 유효 {sum(1 for r in w3 if r.get('tool_valid'))}")
    print(' ', mk, ' | '.join(s))
