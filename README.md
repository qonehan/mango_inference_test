# mango_inference_test

> GitHub 작업 전 [AGENTS.md](AGENTS.md)를 읽습니다. PDF·PPTX·생성 대시보드·측정 원시 데이터는 학교 Google Drive에 있으며 [ASSETS.md](ASSETS.md)로 복원합니다.

Mango Inference가 OpenRouter(OR)에 제공사로 들어갔을 때 얼마나 선택될지를 추정하는 실험입니다. OR 기본 라우팅이 엔드포인트를 고르는 방식을 관측하고, Mango와 OR 엔드포인트의 성능을 같은 조건에서 잰 뒤, 둘을 합쳐 예상 점유율을 계산했습니다.

## 본 실험 (2026-11-20 ~ 11-23)

| | 내용 | 결과 |
|---|---|---|
| **본 실험 1: OR 기본 라우팅 관측** | 서울 VM, 43시간, 4개 모델 기본 라우팅 33,484건 + OR 공개 지표 수집 → 선택 모형(조건부 로짓) | 기본 라우팅은 출력 가격과 TTFT를 가장 크게 반영. GLM-5.3은 Wafer 두 곳이 77% |
| **본 실험 2: Mango와 OR 엔드포인트 성능 비교** | 2026-11-22 하루 4회차, 서울 · 미국 동부 VM 동시. Mango + 모델별 OR 엔드포인트 6개 고정 호출 | Mango 출력 속도는 DeepSeek · GLM-5.3 Flash에서 1~2위, TTFT는 3~7위, 짧은 작업 비용은 대부분 가장 비쌈 |
| **예상 점유율** | 본 실험 2의 Mango 값을 OR 공개 기준으로 환산해 본 실험 1의 선택 모형에 넣음 | 서울 / 미국 측정 기준 DeepSeek 3.9% / 3.8%, GLM-5.3 0.4% / 0.2%, GLM-5.3 Flash 5.7% / 2.1%, Kimi 7.2% / 3.7% |

### 읽는 순서

1. [본 실험 설계](docs/13_본실험_설계.md)
2. [본 실험 결과](docs/14_본실험_결과.md) · [발표 자료(26장)](slides/04_본실험_결과.html)
3. 결과 파일: [본 실험 1](results/main1_routing_20261120/README.md) · [본 실험 2](results/main2_compare_20261122/README.md)
4. 원본 엑셀 4개: [data/main_raw_xlsx_20261123](data/main_raw_xlsx_20261123/README.md)(학교 Drive)
5. 코드: [code/main](code/main/README.md)

**해석 범위.** 본 실험 2는 하루(일요일) 측정이고, 예상 점유율은 그 시간대 공개 통계와 본 실험 1의 선택 모형에 대한 값입니다. 서울 · 미국 동부에서 본 종단간 성능이며 서버 내부 성능으로 일반화하지 않습니다. 자세한 한계는 [13번 5절](docs/13_본실험_설계.md#5-한계)과 [14번 5절](docs/14_본실험_결과.md#5-해석할-때-주의할-점)에 있습니다.

## 사전 실험 (~ 2026-11-19)

본 실험의 측정 방법과 대상을 정하기 위해 먼저 한 실험과 조사입니다.

| 단계 | 내용 | 문서 · 기록 |
|---|---|---|
| 계획 · 프로토콜 | 실험 계획 절차, 프로토콜 v1.0(이전 v0.9.4, [통합 명세 HTML](pdf/04_통합명세_v0.9.2.html)) | [01](docs/01_실험계획_절차.md) · [02](docs/02_실험_프로토콜.md) |
| 간이 테스트 · reasoning 시험 | 호출 방식 · 추론 설정 확인 | [03](docs/03_간이테스트_결과.md) · [04](docs/04_reasoning설정_시험.md) · [05 보완 이력](docs/05_간이테스트_이후_보완.md) |
| 파일럿 (2026-11-17) | 본 루프 1,556건, Broadcast 켬 · 끔 대조 160건, 결합률 100%, Mango 청구 대조 0.1% 일치 | [08](docs/08_파일럿_결과.md) · [원시 기록](results/pilot_20261117/README.md) |
| 1회 실험 (2026-11-17 23:50 ~ 11-18 03:02 KST) | 서울, 4,528건, 확증 판정 12개 중 Mango 우위 5 · 열위 2 · 판정 불가 5(그 시간대 1회 결과) | [09](docs/09_1회실험_결과.md) · [원시 기록](results/run1_20261117/README.md) · [대시보드](slides/1회실험_대시보드.html) |
| OR 공개 통계 분석 (2026-11-18) | 1주 점유율, 성능 · 가격 · 오류율과 점유율 상관, 신규 모델 지원 시점, 공개 기준 Mango 위치 | [10](docs/10_OpenRouter_공개통계_분석.md) · [재수집 집계](data/openrouter_recollect_20261117/README.md) |
| 2회 실험 설계 초안 (2026-11-18) | 고정 대상 선정 규칙 등. 본 실험 설계로 대체 | [11](docs/11_2회실험_설계_초안.md) |
| OR DeepSeek 시간 · 지역 분석 (2026-11-19) | 성능 화면 필터 조합 4,065건. 같은 제공사 처리량은 위치와 무관, 첫 토큰은 서울이 미국보다 약 26% 늦음 | [12](docs/12_OpenRouter_DeepSeek_시간지역_분석.md) · [대시보드](https://drive.google.com/file/d/1IQx14rWnMmHegCraLTzxMXjFoS2hZCOv/view?usp=drivesdk) |
| 참고 | Artificial Analysis 방법론, OR 라우팅 방식 | [06](docs/06_참고_ArtificialAnalysis_방법론.md) · [07](docs/07_참고_OpenRouter_라우팅_방식.md) |

사전 실험의 발표 자료와 3D 그래프는 [slides/](slides/README.md)에 있습니다. 1회 실험에서 배운 점(OR 고정 대상 선정 오류, 클라이언트 캐시 설정 차이 등)은 [09번](docs/09_1회실험_결과.md) 11 · 12절에 있습니다.

## 폴더

`docs/` 문서, `code/` 사전 실험 코드(`code/main/`은 본 실험 코드), `data/` 공개 통계와 본 실험 원본 엑셀, `results/` 측정 결과(`main1_*`·`main2_*`는 본 실험, 나머지는 사전 실험), `slides/` 발표 자료, `pdf/` 사전 실험 명세 보관본(현재 기준 아님).

## 검증과 실행 (사전 실험 측정기)

```bash
python -m unittest discover -s code -p "test_*.py"
python code/verify_meter.py --out meter_check.json   # 모의 서버, 과금 없음
python code/smoke_test.py --dry-run
```

실제 호출은 과금됩니다. 키는 환경변수 `MANGO_API_KEY`, `OPENROUTER_API_KEY` 또는 저장소 밖 `%USERPROFILE%\mango_bench.env`에서 읽습니다. 본 실험 코드는 [code/main](code/main/README.md)을 봅니다.
