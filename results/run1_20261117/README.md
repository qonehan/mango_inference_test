# 1회 실험 원시 기록 (2026-11-17, 프로토콜 v1.0)

[1회 실험 결과](../../docs/09_1회실험_결과.md)의 근거 데이터입니다. 서울 VM(AWS ap-northeast-2, m6i.large), 측정 코드 커밋 `5e9962d`, 설정 `code/config/run1.yaml`(SHA-256 `a970b7e0…d2ad16`).

- **실행 시각:** 본 루프 2026-11-17 14:50:47~18:02:26 UTC(**2026-11-17 23:50 ~ 2026-11-18 03:02 KST**). 품질 점검 Q는 18:09~18:19 UTC(03:09~03:19 KST)입니다.
- **판정 범위:** 이 기록은 **그 시간대의 1회 결과**입니다. 다른 시간대나 다른 날로 일반화하지 않습니다(프로토콜 0절).

| 파일 | 내용 |
|---|---|
| `raw.jsonl.gz` | 본 루프 4,528건과 준비 호출 52건의 측정기 기록. HTTP 응답 헤더(`headers`)만 뺐습니다 |
| `generations.jsonl.gz` | OpenRouter generation 조회 2,578건(제공사·엔드포인트·청구액) |
| `check.json` | 회차 점검(결측 0, generation·Broadcast 결합률 100%, 셀별 실패율, 지출) |
| `manifest.json`, `probes.jsonl` | 실행 환경, 설정·질문 세트 해시, 봉인 해시, 접속 주소의 ASN·경로·연결 시간. VM 호스트명은 뺐습니다 |
| `control.jsonl` | 대조 호스트 왕복 시간(1분 간격) |
| `mango_models.json`, `or_endpoints.json` | 실행 시점의 Mango `GET /v1/models`(정가)와 OpenRouter 엔드포인트 목록 |
| `or_stats.jsonl.gz` | 실행 중 30분마다 저장한 OpenRouter 모델 성능 페이지의 30분 창 통계(8회) |
| `q_graded.jsonl` | Q 25문항 × M·OR-기본 채점(문항 ID·정오답만) |
| `analysis/primary.json` | `code/analyze.py`의 P1~P4 판정 |
| `analysis/routing.csv` | OR 경로별 제공사·엔드포인트 배분 |
| `analysis/deep_analysis.txt` | `code/analyze_pilot.py` 보조 지표 출력(아래 주의 참고) |
| `analysis/compare_or_stats.txt` | `code/compare_or_stats.py` 출력: OR 엔드포인트별 실측·generation 기록·공개 통계 비교 |
| `analysis/explore_run1.txt` | `code/explore_run1.py` 출력: 입력 길이·부하·캐시 대조·고정 제공사·도구 호출 탐색 집계 |
| `analysis/policy_mix.json` | `code/estimate_policy_mix.py` 출력: OpenRouter 전체 요청 중 정책별 비중의 범위 추정(신뢰도 낮음, [09번 12절](../../docs/09_1회실험_결과.md)). 이 폴더의 공개 기록으로 같은 값이 재현됨 (2026-11-18 추가) |
| `SHA256SUMS` | 이 폴더 파일의 해시 |

이 기록으로 만든 대시보드는 [slides/1회실험_대시보드.html](../../slides/1회실험_대시보드.html)에 있습니다(2026-11-19 추가).

**싣지 않은 것:** Broadcast 수신 원본(요청 전문과 계정 메타데이터 포함), Q 응답 원문(`q_raw.jsonl`), 질문 세트 파일(파일럿과 같은 `prompts_manifest`, `code/build_prompts.py`로 재생성), OR 통계 페이지 HTML, 계정 콘솔 캡처. 이 자료는 비공개로 보관합니다.

**`deep_analysis.txt` 주의:** 커밋 `5e9962d`의 `analyze_pilot.py`는 W2 추론 구간이 0인 세션에서 0으로 나누기 오류가 났습니다. 그래서 그 세션을 비율에서 빼는 한 줄 수정본으로 출력했습니다(이 브랜치의 다음 커밋, [프로토콜 13절](../../docs/02_실험_프로토콜.md#13-이탈검토-기록)). 판정 지표와 파일럿 출력 수치는 바뀌지 않습니다. 9·10절 제목의 "경로당 20문항", "파일럿 본 루프"는 파일럿용 문구가 남은 것이고, 실제로는 25문항과 1회 실험 본 루프입니다.
