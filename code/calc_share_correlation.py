"""발표 자료 slides/01_추론서비스_경쟁력검토.html 5쪽의 성능·가격·오류율과 최근 1주 점유율의 스피어만 순위 상관.

입력: slides/02_제공사_성능가격점유율_3D.html에 내장된 표 데이터(DALL). 네트워크 호출 없음. 3D 그래프 하단 상관 패널과 같은 기준.
기준: 모델별로 최근 1주(2026-11-10~11-16) 점유율 0 초과, 표준 등급 엔드포인트만(Fast·Flex 등 등급 제외), Mango 제외.
     점유율은 제공사 전체 값(등급 점이 있으면 share_full). 2026-11-18에 30일 → 1주로 바꿈(신규 모델의 진입 시점 차이).
     가격은 3D 자료의 가중 실사용 단가(price), 성능은 공개 1주 평균(perf). 동순위는 평균 순위.
출력: data/openrouter_recollect_20261117/share_correlation.csv, 표준 출력.

실행: python code/calc_share_correlation.py
"""
import csv, json, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, 'slides', '02_제공사_성능가격점유율_3D.html')
OUT = os.path.join(BASE, 'data', 'openrouter_recollect_20261117', 'share_correlation.csv')
KEYS = [('price', '가격'), ('speed', '처리속도'), ('ttft', '첫 토큰 지연'), ('tool', '툴 호출 오류율'), ('struct', '구조화 출력 오류율'), ('cache', '캐시 적중률')]


def rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r, i = [0.0] * len(v), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = rank(a), rank(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    return cov / (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5


def main():
    s = open(SRC, encoding='utf-8').read()
    i = s.find('const DALL=') + len('const DALL=')
    data, _ = json.JSONDecoder().raw_decode(s[i:])
    rows = []
    for model in sorted({d['model'] for d in data}):
        sh = lambda d: d['share_full'] if d.get('share_full') is not None else d['share']
        sel = [d for d in data if d['model'] == model and (sh(d) or 0) > 0
               and not d.get('tier') and 'mango' not in d['provider'].lower()]
        row = dict(model=model, providers=len(sel))
        for key, _ in KEYS:
            pts = [(d['price'] if key == 'price' else d['perf'].get(key), sh(d)) for d in sel]
            pts = [p for p in pts if p[0] is not None]
            row[key] = round(spearman([p[0] for p in pts], [p[1] for p in pts]), 2)
            row[key + '_n'] = len(pts)
        rows.append(row)
    with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    for r in rows:
        print(r)


if __name__ == '__main__':
    main()
