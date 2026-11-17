# mango_inference_test

Mango Inference(MangoBoost)와 OpenRouter 제공사의 성능·비용 비교, 그리고 OpenRouter가 실제로 어느 제공사를 고르는지를 재는 실험의 계획·시험 기록·코드입니다.

- **상태:** 프로토콜 v0.9(사전 등록 초안). 간이 테스트와 reasoning 설정 시험을 마치고 보완을 반영함. 파일럿과 본 측정은 아직 실행하지 않음
- **대상:** DeepSeek V4.1 Flash 1017, GLM-5.3, GLM-5.3-Flash, Kimi K3.1 Pro × 호출 경로 12개(Mango 직접, OpenRouter 기본, 고정 엔드포인트 5, 정렬 3, `:floor`·`:nitro`)
- **규모·예산:** 약 26,200건, 약 $100~215 (상한 $250)

## 먼저 볼 문서

| 문서 | 내용 |
|---|---|
| [실험 진행 정리 (PDF, 5쪽)](pdf/02_실험진행_정리.pdf) | 언제 몇 번 돌리는지, 질문 예시, 회차별 호출 수, API 요청 예시, 예산 |
| [재현용 명세 (PDF, 6쪽)](pdf/01_재현용_명세.pdf) | 설정값·질문 생성 규칙·시드·실행 순서·지표 공식·분석 절차 (설정의 기준) |
| [실험 계획 절차](docs/01_실험계획_절차.md) | 계획을 세운 순서, 단계별 산출물, 재사용 체크리스트 |
| [실험 프로토콜](docs/02_실험_프로토콜.md) | 연구 질문·가설·판정 기준·통제 방법과 설계 이유 (변경 기록 포함) |
| [간이 테스트 이후 보완 이력](docs/05_간이테스트_이후_보완.md) | 시험에서 발견한 것과 그에 따라 바뀐 계획 20건 |

## 폴더

| 폴더 | 내용 |
|---|---|
| `docs/` | 절차, 프로토콜, 간이 테스트 결과, reasoning 설정 시험, 보완 이력, 참고 자료(AA 방법론, OpenRouter 라우팅) |
| `pdf/` | 재현용 명세, 실험 진행 정리 (PDF와 원본 HTML) |
| `code/` | 간이 테스트(`smoke_test.py`), reasoning 시험(`reasoning_param_test.py`), 응답 엔드포인트 조회(`enrich_endpoints.py`), 결과 페이지 생성, 엔드포인트 통계 생성 |
| `results/` | 간이 테스트·reasoning 시험 원시 기록(JSONL), 요약 CSV, 결과 페이지(HTML) |
| `data/` | OpenRouter 엔드포인트별 공개 통계, 30일 점유율, 가격 비교 (2026-11-16 수집) |

## 코드 실행

```bash
python -m venv .venv && .venv/Scripts/pip install openai httpx
python code/smoke_test.py --dry-run                 # 호출 없이 요청 목록 확인
python code/smoke_test.py --out results/<폴더>      # 경로마다 1회 호출
python code/enrich_endpoints.py results/<폴더>      # 응답한 엔드포인트 채우기
```

API 키는 환경변수 `MANGO_API_KEY`, `OPENROUTER_API_KEY` 또는 저장소 밖 `%USERPROFILE%\mango_bench.env`(KEY=VALUE)에서 읽습니다. 키는 저장소에 넣지 않습니다.

