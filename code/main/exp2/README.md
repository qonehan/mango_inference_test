# 본 실험 2 코드 (Mango vs OR 엔드포인트)

| 파일 | 역할 |
|---|---|
| `runner_20261122.py` | 측정기. 경로(Mango · OR 고정 엔드포인트)와 요청 종류(S · G · L · T · J)별 호출, TTFT · 속도 · 토큰 · 비용 기록 |
| `build_requests_20261122.py` | 요청 세트 생성 |
| `judge_20261122.py` | 도구 호출 · 구조화 출력 응답 판정 |
| `collect_public_20261122.py` | 측정 중 OR 공개 지표 수집(colo ICN · IAD 포함) |
| `schedule_20261122.sh` | 회차 실행 일정 |
| `recompute_local_20261124.py` | 원본에서 VM별 경로 집계 재계산(`summary_*_local`, `refs_public_local`) |
| `make_variant_green_20261125.py` | TTFT 환산(모델 서버 첫 응답 기준) 재료 계산(`green_factor`) |
| `share_2_2_period_20261125.py` | 동일 가중 환산과 측정 시간대 예상 점유율(최종). `--check`는 이전 방식 재현 |
| `export_raw_xlsx_20261123.py` | 원본 JSONL을 엑셀로 펼침([data/main_raw_xlsx_20261123](../../../data/main_raw_xlsx_20261123/README.md)) |

출력: [results/main2_compare_20261122](../../../results/main2_compare_20261122/README.md).
