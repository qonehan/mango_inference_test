# 본 실험 2 결과 (Mango vs OR 엔드포인트, 2026-11-22 서울 · 미국 동부)

설계: [docs/13](../../docs/13_본실험_설계.md) 3 · 4절 · 해석: [docs/14](../../docs/14_본실험_결과.md) 2~4절 · 코드: [code/main/exp2](../../code/main/exp2/README.md).

| 파일 | 내용 |
|---|---|
| `summary_seoul_local_20261124.json`, `summary_iad_local_20261124.json` | VM별 경로 집계(TTFT · 속도 · 비용 · 표본 수) |
| `refs_public_local_20261124.json` | 회차별 OR 공개 p50(측정 시간 + 10분 안 스냅숏) |
| `green_factor_20261125.json` | 모델 서버 첫 응답 기준 TTFT 환산 재료 |
| `share_2_2_period_20261125.json`·`.txt` | 동일 가중 환산 계수, 측정 시간대 예상 점유율, 후보별 지표와 순위(발표 자료 3번 · 부록) |

요청별 원본과 공개 지표 원본은 [data/main_raw_xlsx_20261123](../../data/main_raw_xlsx_20261123/README.md)의 엑셀에 있습니다.
