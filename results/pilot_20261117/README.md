# 파일럿 원시 기록 (2026-11-17)

> 원시 데이터 파일(표의 jsonl·gz·json·SHA256SUMS)은 2026-11-25부터 학교 Google Drive에 보관합니다. 위치와 해시는 [ASSETS.md](../../ASSETS.md), 로컬 복원은 `python -I tools/cloud_assets.py sync`. `analysis/`와 이 README는 GitHub에 있습니다.

[파일럿 결과와 결론](../../docs/08_파일럿_결과.md)의 근거 데이터입니다. 서울 VM(AWS ap-northeast-2), 측정 코드 커밋 `48f7bb0`, 설정 `code/config/pilot.yaml`. **본 판정에 쓰지 않는 파일럿 데이터입니다.**

| 파일 | 내용 |
|---|---|
| `raw.jsonl.gz` | 본 루프 요청 1,604건(준비 호출 48건 포함, 본 루프 1,556건). 측정기 기록 그대로이고 HTTP 응답 헤더만 뺐습니다 |
| `generations.jsonl.gz` | OpenRouter generation 조회 결과(제공사·엔드포인트·단가·청구액) |
| `check.json` | 회차 점검 결과(결측·결합률·실패율·지출) |
| `manifest.json`, `probes.jsonl` | 실행 환경, 설정·질문 세트 해시, 접속 주소의 ASN·경로·연결 시간. VM 호스트명은 뺐습니다 |
| `control.jsonl` | 대조 호스트 왕복 시간(로컬 장애 확인용, 1분 간격) |
| `mango_models.json`, `or_endpoints.json` | 실행 시점 Mango `GET /v1/models`(정가·양자화)와 OpenRouter 엔드포인트 목록 |
| `q_graded.jsonl` | 품질 점검 Q 채점 결과(문항 ID·정오답만). 문항 원문과 모델 응답은 싣지 않습니다(`q_items.jsonl`처럼 저장소에서 제외) |
| `broadcast_ab/` | Broadcast 켬·끔 대조 160건 원시 기록(헤더 제외), generation, 요약 |
| `analysis/primary.json`, `analysis/routing.csv` | `code/analyze.py`를 파일럿에 적용한 P1~P4와 라우팅 배분 |
| `analysis/pilot_w2_n.json` | W2 세션 수 산정(2-3절) |
| `analysis/deep_analysis.txt` | `python code/analyze_pilot.py results/pilot_20261117` 출력 |
| `SHA256SUMS` | 이 폴더 파일의 해시 |

싣지 않은 것: Broadcast 수신 원본(요청 전문과 계정 메타데이터 포함), 질문 세트 파일(`code/build_prompts.py`로 재생성, 해시는 `manifest.json`), 도구 호출 버그로 중단한 1차 실행 173건, 계정 콘솔 캡처. 이 자료는 비공개로 보관합니다.

질문 세트 재생성 후 해시 대조:

```bash
# CPython v3.12.7 소스를 받아 경로를 넘깁니다. 시드 20261116은 코드에 고정
python code/build_prompts.py --cpython <cpython-v3.12.7 경로> --out prompts
```

생성된 `prompts/manifest.json`의 해시를 이 폴더 `manifest.json`의 `prompts_manifest`와 비교합니다.
