# OpenRouter 공개 화면 재수집 집계 (2026-11-17)

2026-11-17에 OpenRouter 모델 페이지·엔드포인트 상세·제공사 페이지에 표시된 값을 브라우저로 읽어 만든 집계입니다. 내부 API는 호출하지 않았습니다. 원시 화면 수집본과 수집·집계 코드는 싣지 않았습니다. 해석은 [docs/10_OpenRouter_공개통계_분석.md](../../docs/10_OpenRouter_공개통계_분석.md)에 있습니다.

| 파일 | 내용 |
|---|---|
| `share_7d.csv` | 모델×제공사 최근 1주(2026-11-10~11-16) 토큰·점유율. 30일 값(`tokens_30d`, `share_30d_pct`)을 함께 둠. **점유율 기준 파일** |
| `share_30d.csv` | 모델×제공사 30일 토큰·점유율(10-18~11-16, Kimi는 출시일 10-23부터). 신규 모델의 진입 시점 차이가 섞여 참고용 |
| `model_summary.csv` | 모델별 30일 토큰, 입력 비중, 가중 캐시 적중률, 활동 제공사 수, 제공사 합계 일치율(`coverage_*`) |
| `provider_first.csv` | 제공사별 그 모델 사용량 첫 관측일(실제 지원 시작일의 상한)과 엔드포인트 차트 첫 날짜 |
| `kimi_official_share_periods.csv` | Kimi K3.1 Pro 공식 Moonshot 점유율의 기간별 값(단독 판매·가중치 공개 첫 주·최근 1주·30일) |
| `kimi_provider_share_daily.csv` | Kimi K3.1 Pro 일별 모델 토큰, 활동 제공사 수, 주요 제공사(Moonshot·Alibaba·inference.net·Wafer) 점유율 |
| `share_correlation.csv` | 성능·가격·오류율과 최근 1주 점유율의 스피어만 순위 상관. [code/calc_share_correlation.py](../../code/calc_share_correlation.py)가 [3D 그래프](../../slides/02_제공사_성능가격점유율_3D.html) 내장 데이터로 다시 만듦 |

**정의**

- 점유율 = 제공사 일별 토큰 합 ÷ 모델 Activity 전체 토큰.
- 제공사 일별 토큰은 엔드포인트 상세 차트(제공사 단위 값)를 우선으로 씁니다. 빈 날은 제공사 페이지 툴팁으로 보충합니다. 툴팁에 대상 모델이 없던 날은 0으로 두고, 그 몫은 미귀속에 남습니다.
- 입력 비중 = 30일 Prompt ÷ (Prompt + Reasoning + Completion).
- 캐시 적중 = 엔드포인트 캐시 적중률을 1일 토큰 비중으로 가중 평균한 값.
- 값은 화면 표시값(반올림)입니다.
