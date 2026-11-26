# 본 실험 원본 엑셀 (2026-11-23)

본 실험 1 · 2의 요청별 원본과 OR 공개 지표 스냅숏 원본을 엑셀 4개로 펼쳤습니다. 각 파일의 첫 시트 `설명`에 열 정의가 있습니다(시트 안의 `2-1회`는 본 실험 1, `2-2회`는 본 실험 2). 코드: [export_raw_xlsx_20261123.py](../../code/main/exp2/export_raw_xlsx_20261123.py). 파일은 학교 Drive에 있으며 [ASSETS.md](../../ASSETS.md)로 복원합니다.

| 파일 | 내용 | 행 |
|---|---|---:|
| [본실험1_요청별_원본_20261123.xlsx](https://drive.google.com/file/d/1EG3KIJ-eE_5c5B8OizPwV1fbbzhUaHm2/view?usp=drivesdk) | OR 기본 라우팅 호출 1건 = 1행(본 실험 34,400 · 긴 입력 대조군 7,144). 첫 시도 엔드포인트 · 시도 내역 · 토큰 · 비용 · generation 지연 | 41,544 |
| [본실험1_공개지표_원본_20261123.xlsx](https://drive.google.com/file/d/1CECGY1PZE6gpIGHNXmBE-sXfLyB4JAT_/view?usp=drivesdk) | OR 공개 지표 스냅숏(엔드포인트 통계 5분 · 엔드포인트 목록 1분 · 시간칸 지표 등 10시트), 2026-11-20 10시 ~ 11-22 04시 UTC | – |
| [본실험2_요청별_원본_20261123.xlsx](https://drive.google.com/file/d/1zH5jEyw23WG8MGLc8enA3MsectsD68tI/view?usp=drivesdk) | Mango · OR 고정 경로 요청 1건 = 1행(서울 23,224 · 미국 동부 5,264; S · L · G · T · J · 예열). TTFT · 속도 · 토큰 · 판정 · OR generation 비용 | 28,488 |
| [본실험2_공개지표_원본_20261123.xlsx](https://drive.google.com/file/d/1cC5ciMyVzTzP0WpvPN0qOTK-UU0Xsrux/view?usp=drivesdk) | 서울 VM 수집 공개 지표(2026-11-22 04:55 ~ 11-23 02:00 UTC, colo ICN · IAD 포함) | – |

- 공개본에서는 본실험2 요청별 파일의 `system_fingerprint` 열(Mango 서버 구성 정보)을 지웠습니다. 나머지 세 파일은 작업본과 같습니다.
- 엑셀 셀 한도(32,767자)를 넘는 본문은 잘렸고(`…[잘림]`), 스트리밍 조각 · 응답 헤더는 크기 때문에 뺐습니다.
