# 본 실험 1 코드 (OR 기본 라우팅 관측)

| 파일 | 역할 |
|---|---|
| `exp2_1_routing_20261120.py` | 기본 라우팅 호출(모델당 매시 10분, 3초 간격)과 OR 공개 지표 수집 |
| `exp2_1_long_input_control_20261121.py` | 긴 입력(약 3,100토큰) 대조군 호출 |
| `analyze_exp2_1_choice_20261121.py` | 요청 · generation · 공개 스냅숏 결합, 조건부 로짓(선택 모형) |
| `refit_by_model_20261123.py` | 모델별 계수 재적합(최종 사양 `S8_eff1h_split`) |
| `decompose_endpoint_20261124.py` | 엔드포인트별 실제 몫 · 예측 몫 · 지표 평균(발표 자료 1번 표) |
| `select_fixed_providers_20261122.py` | 처음 39시간 집계로 본 실험 2 고정 경로 후보 선정 |

출력: [results/main1_routing_20261120](../../../results/main1_routing_20261120/README.md).
