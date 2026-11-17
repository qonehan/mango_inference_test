# reasoning 설정 시험 (2026-11-17)

각 엔드포인트가 reasoning 켜기·끄기 지정을 실제로 따르는지 확인한 기록입니다. [간이 테스트](03_간이테스트_결과.md)에서 같은 질문에 reasoning 토큰이 엔드포인트마다 0~599개로 달라, 기본값에 맡기면 조건이 통제되지 않는 문제가 드러났기 때문입니다.

- 코드: [reasoning_param_test.py](../code/reasoning_param_test.py)
- 원본: `raw.jsonl`, 요약: `summary.csv`. 152건(모델 4 × [Mango 설정 4 + OpenRouter 엔드포인트 5 × 설정 3] × 2회), OpenRouter 비용 약 $0.015
- 질문: "A train travels 60 km in 45 minutes. What is its average speed in km/h? Reply with the number only." (정답 80)
- OpenRouter는 제공사가 아니라 구체 엔드포인트 슬러그로 고정(`"order": ["wafer/us"]` 등)

## 시험한 설정

| 대상 | 설정 이름 | 보낸 값 |
|---|---|---|
| Mango | default | 없음 |
| Mango | off-template | `chat_template_kwargs: {"enable_thinking": false, "thinking": false}` (문서에 없는 전달 필드) |
| Mango | on-template | `chat_template_kwargs: {"enable_thinking": true, "thinking": true}` |
| Mango | effort-low | `reasoning_effort: "low"` |
| OpenRouter | default / off / on | 없음 / `reasoning: {"enabled": false}` / `reasoning: {"enabled": true}` |

Mango 공식 문서는 "reasoning 조절 파라미터가 문서화되어 있지 않고, 문서에 없는 필드는 모델 서버로 그대로 전달된다"고 안내합니다.

## 결과 (reasoning 토큰 수, 2회)

| 모델 | 기본값 | 끄기 | 강도 낮춤 |
|---|---|---|---|
| DeepSeek V4.1 Flash 1017 | 모든 곳에서 켜짐 (28~45) | **모든 곳에서 꺼짐 (0)**: Mango는 `chat_template_kwargs`, OpenRouter는 `reasoning.enabled=false` | Mango `reasoning_effort: low` 무시 (32~35) |
| Kimi K3.1 Pro | 모든 곳에서 켜짐 (30~52) | **응답한 곳은 모두 꺼짐 (0)**: Mango, Moonshot, inference.net, Modal | Mango `low`로 줄어듦 (15~17) |
| GLM-5.3 | 켜짐 (32~57) | **끌 수 없음.** OpenRouter 전 엔드포인트 400 "Reasoning is mandatory". Mango는 오류 없이 받지만 답이 비거나 4,099토큰까지 추론이 폭주 | Mango `low`로 거의 0 (1) |
| GLM-5.3-Flash | 켜짐 (14~48) | **끌 수 없음.** OpenRouter 400, Mango 400 "always engages in thinking … use reasoning_effort low, high or max" | Mango `low`로 줄어듦 (7~11) |

- `on` 지정은 모든 곳에서 기본값과 비슷한 수준이었습니다. 기본값이 곧 "켜짐"입니다.
- 오답은 없었습니다(응답한 경우 모두 80).

## 실패 40건

| 원인 | 건수 | 의미 |
|---|---:|---|
| 400 reasoning 끄기 불가 (GLM 계열) | 22 | 모델 특성. 설정 선택지에서 제외 |
| Mango GLM-5.3 끄기 요청 시 추론 폭주 (출력 상한 도달) | 1 | 문서에 없는 경로의 오작동 |
| 429 `new-account-rpm`: **새 계정은 Kimi K3.1 Pro를 분당 20회까지만 호출 가능** | 10 | OpenRouter 계정 한도. 본 실험에서 우리 쪽 제한이 되지 않도록 모델별 호출 속도를 조절해야 함 |
| 429 "Provider returned error" (Wafer·inference.net·Together) | 7 | 제공사 쪽 한도. 본 실험에서는 제공사 쪽 실패로 집계 |

GLM-5.3 Wafer(`wafer/us`)는 이번에 전부 429여서, 간이 테스트에서 reasoning이 0~1개로 나온 이유는 확인하지 못했습니다.

## 추가 시험: OpenRouter 강도 낮춤 (2026-11-17)

처음 시험에서 강도 낮춤(`reasoning_effort`)을 Mango에만 넣고 OpenRouter에는 넣지 않아 비교가 비대칭이었습니다. 같은 엔드포인트에 `reasoning: {"effort": "low"}`를 2회씩 보냈습니다(40건, 38건 응답, 2건은 Open Inference 제공사 429). 결과는 `or_effort_low/`.

| 모델 | OpenRouter 강도 낮춤 | Mango 강도 낮춤 (처음 시험) | 기본값 |
|---|---|---|---|
| DeepSeek V4.1 Flash 1017 | 27~43 (변화 없음) | 32~35 (변화 없음) | 28~45 |
| GLM-5.3 | 0~7 | 1 | 32~57 |
| GLM-5.3-Flash | 0~9 | 7~11 | 14~48 |
| Kimi K3.1 Pro | 18~40 | 15~17 | 30~52 |

- Mango의 비공식 `reasoning_effort`와 OpenRouter의 공식 `reasoning.effort`가 **같은 방향으로 작동**합니다. GLM·Kimi는 줄고 DeepSeek은 변화가 없습니다.
- GLM 계열은 "끄기"는 안 되지만 강도 낮춤으로 0개까지 줄어드는 응답이 있었습니다(Z.ai, SiliconFlow, Decart). 이 경우 켜짐 지정과 달리 0은 정상 동작입니다.
- 모든 응답이 정답(80)이었습니다.

## TTFT 두 가지

모든 응답에 첫 토큰 시간(`ttft_any_ms`, 생각 포함)과 첫 답변 시간(`ttft_content_ms`)을 함께 기록했습니다. 결과 페이지의 각 칸에 두 값의 중앙값을 함께 표시합니다. 생각을 스트림으로 보내지 않는 엔드포인트는 두 값이 같아지므로, 스트림으로 받은 reasoning 글자 수(`reasoning_chars`)도 기록합니다.

## 프로토콜에 주는 결론

1. **reasoning은 "켜짐"으로 명시해 통제할 수 있습니다.** 모든 모델·엔드포인트에서 기본값이 켜짐이고, GLM 계열은 끌 수도 없습니다. 끄기를 표준 조건으로 삼을 수 없으므로 켜짐(OpenRouter `reasoning.enabled=true`)을 명시하고, 응답마다 reasoning 토큰 수로 실제로 켜졌는지 확인합니다.
2. **reasoning 토큰이 0인 응답은 따로 표시합니다.** 켜짐을 지정했는데 0이면 그 엔드포인트가 지정을 따르지 않은 것이므로 성능 비교에서 구분합니다(간이 테스트의 GLM-5.3 Wafer 같은 경우).
3. **Mango에는 켜기 지정을 보내지 않습니다.** 기본값이 켜짐이고, `chat_template_kwargs`는 문서에 없는 경로이며 GLM-5.3에서는 오작동했습니다. 요청 본문 차이는 실행 기록에 남깁니다.
4. **OpenRouter 새 계정 한도(모델당 분당 20회)를 실행 계획에 반영해야 합니다.** 모델별 호출 속도를 분당 20회 아래로 묶거나, 한도가 풀리는 조건을 확인합니다.
