# 클라우드 원본 자료

원본 보관: **qonehan@snu.ac.kr**의 [mango_inference_test 폴더](https://drive.google.com/drive/folders/1u3SObRT9STnyLYf4wlUQnRBa-mTme4ep) (상위 `github` 폴더 권한 상속).
GitHub 작업 전에 [공통 규칙(GITHUB_CLOUD_SYSTEM.md)](https://drive.google.com/file/d/1KNLYcYaBB8FmGaUEuskLlHzwj40yJHMU/view)을 먼저 읽습니다.

2026-11-25, 커밋 `d473875` 기준 원본 41개(21,508,272바이트)를 업로드하고 전부 재다운로드해 SHA-256 일치를 확인했습니다. 내보낸 PDF 3개, PPTX 2개, 생성 대시보드 HTML 1개(`slides/시간지역_성능_대시보드.html`), 파일럿·1회 실험·reasoning 시험·간이 테스트의 측정 원시 데이터입니다. Git에서는 현재 추적만 제거했으며 과거 커밋 기록은 남습니다. 코드·문서·분석 결과(`analysis/`, `report_tables/`, `summary.csv`)·직접 작성한 HTML은 GitHub에 유지합니다.

## 로컬 복원

```bash
python -I tools/cloud_assets.py pull   # 원본 캐시 보관 → git pull --ff-only → 복원
python -I tools/cloud_assets.py sync   # 복원만 (git cloud-sync 별칭 가능)
```

인증: `gcloud auth login qonehan@snu.ac.kr --enable-gdrive-access`. 토큰은 저장하지 않습니다. 로컬 원본이 이미 있고 해시가 맞거나 검증된 캐시(`.git/cloud-assets-cache`)가 있으면 인증 없이 복원합니다. 로컬 수정본은 덮어쓰지 않고 보고합니다. 업로드는 `tools/cloud_assets.py upload <경로목록>`, 커밋 전 점검은 `tools/cloud_assets.py check`.

분석 코드가 읽는 상대 경로는 그대로이며, 실행 전 원본을 복원합니다. 원본은 Git blob(LF) 바이트 그대로 올렸으므로 `results/*/SHA256SUMS`로도 검증됩니다. 상세 SHA-256과 Git blob SHA는 [manifest](assets.manifest.json)에 있습니다.

| 로컬 경로 | 원본 | 크기(bytes) |
|---|---|---:|
| `pdf/01_재현용_명세.pdf` | [Drive](https://drive.google.com/file/d/1KX2TDwB4Xjd0p-ody19sTScyWEHGM_Ye/view?usp=drivesdk) | 880,598 |
| `pdf/02_실험진행_정리.pdf` | [Drive](https://drive.google.com/file/d/1ZW-WizCoGCsUxpxe31-cAjtRAn6lJWJs/view?usp=drivesdk) | 738,135 |
| `pdf/03_정정명세_v0.9.1.pdf` | [Drive](https://drive.google.com/file/d/1Tni2Sji8GQ47l2PTlEfXY9Aic4E5fA4Y/view?usp=drivesdk) | 118,009 |
| `results/pilot_20261117/SHA256SUMS` | [Drive](https://drive.google.com/file/d/11kpZFtMVrbfKqz5bxzlt7lRrfYxhCWIw/view?usp=drivesdk) | 1,406 |
| `results/pilot_20261117/broadcast_ab/generations.jsonl.gz` | [Drive](https://drive.google.com/file/d/1IE_gW7wG9gn4wdZVuqougqMg_-Ch7It8/view?usp=drivesdk) | 17,650 |
| `results/pilot_20261117/broadcast_ab/raw.jsonl.gz` | [Drive](https://drive.google.com/file/d/18FB-8bn8npOLnvJ3u6it21NAFRljpCGV/view?usp=drivesdk) | 54,101 |
| `results/pilot_20261117/broadcast_ab/summary.json` | [Drive](https://drive.google.com/file/d/1KbJnk8VBfzGnwDU_qu6i3BGcoyNY4_pd/view?usp=drivesdk) | 730 |
| `results/pilot_20261117/check.json` | [Drive](https://drive.google.com/file/d/1wk5hIFRgeXYbmmLXhR9Vl9Wc5qxyF-mL/view?usp=drivesdk) | 10,766 |
| `results/pilot_20261117/control.jsonl` | [Drive](https://drive.google.com/file/d/1nGelN17TEH2-LJkM0NSrTtfsE3PanPjS/view?usp=drivesdk) | 7,380 |
| `results/pilot_20261117/generations.jsonl.gz` | [Drive](https://drive.google.com/file/d/1rImFYqb6wddfJ4KiOo-pwqDgrD_bWXjy/view?usp=drivesdk) | 114,275 |
| `results/pilot_20261117/mango_models.json` | [Drive](https://drive.google.com/file/d/12GY6VVFfXOoKVdyhAa98qVK3wucbXGO0/view?usp=drivesdk) | 2,689 |
| `results/pilot_20261117/manifest.json` | [Drive](https://drive.google.com/file/d/1T7F5TNoXPqmXHLF4_42kF-wcHM85atav/view?usp=drivesdk) | 3,541 |
| `results/pilot_20261117/or_endpoints.json` | [Drive](https://drive.google.com/file/d/16m_lBJAX4odD5rUSk-xggAQUwF4RheLh/view?usp=drivesdk) | 192,644 |
| `results/pilot_20261117/probes.jsonl` | [Drive](https://drive.google.com/file/d/1L-vHayYlBiA3MHnF5PLCjs57FWDl9Wqi/view?usp=drivesdk) | 2,953 |
| `results/pilot_20261117/q_graded.jsonl` | [Drive](https://drive.google.com/file/d/1NvvPDwRolhvWVcK-vH_djDT7nHQbjy8p/view?usp=drivesdk) | 80,920 |
| `results/pilot_20261117/raw.jsonl.gz` | [Drive](https://drive.google.com/file/d/1JZFjNMT8niDny9hOjFOXNo_9suWwSaPE/view?usp=drivesdk) | 3,629,411 |
| `results/reasoning_test_20261117/endpoints.jsonl` | [Drive](https://drive.google.com/file/d/1ZsueNyfRsgrqbv0EuZWek9fiFJE9nKjo/view?usp=drivesdk) | 47,301 |
| `results/reasoning_test_20261117/or_effort_low/endpoints.jsonl` | [Drive](https://drive.google.com/file/d/1lDGUJLb0RWBew-ci7JdVW3VB_sOXZcoB/view?usp=drivesdk) | 21,380 |
| `results/reasoning_test_20261117/or_effort_low/raw.jsonl` | [Drive](https://drive.google.com/file/d/1lF8o_Dn8zGNGxlitdpXQ9pxJpVdMWct5/view?usp=drivesdk) | 66,425 |
| `results/reasoning_test_20261117/raw.jsonl` | [Drive](https://drive.google.com/file/d/1ze8qAfCDOcDrnqCEm9SSD2pfJd0Jsc5n/view?usp=drivesdk) | 194,224 |
| `results/run1_20261117/SHA256SUMS` | [Drive](https://drive.google.com/file/d/1l7SWVhLEZ7pi2t8kvwCRJCaPkmrBEKIt/view?usp=drivesdk) | 1,394 |
| `results/run1_20261117/check.json` | [Drive](https://drive.google.com/file/d/1FfW6bq6sRmlIIXyIx-Af_96QRNaSwRQp/view?usp=drivesdk) | 11,575 |
| `results/run1_20261117/control.jsonl` | [Drive](https://drive.google.com/file/d/1MA9VbZbfJwlvDxdhAIwNRvhLmzhev0rt/view?usp=drivesdk) | 21,785 |
| `results/run1_20261117/generations.jsonl.gz` | [Drive](https://drive.google.com/file/d/1SSMY834RJEIBf-oJA5Wu3Wq1cVWGE4y4/view?usp=drivesdk) | 244,240 |
| `results/run1_20261117/mango_models.json` | [Drive](https://drive.google.com/file/d/1quE7V9uvD_7e6XjI1YGXT0fnhO3tQKZp/view?usp=drivesdk) | 2,689 |
| `results/run1_20261117/manifest.json` | [Drive](https://drive.google.com/file/d/1GGuzU8T5OnlbGJzpR9EMLobUfgZrx0Xz/view?usp=drivesdk) | 3,899 |
| `results/run1_20261117/or_endpoints.json` | [Drive](https://drive.google.com/file/d/1_Cd3TVE5z4Vt2YQd-PYiBaaQeOGLtmZz/view?usp=drivesdk) | 193,744 |
| `results/run1_20261117/or_stats.jsonl.gz` | [Drive](https://drive.google.com/file/d/1qkmpbsqaNktzmWJCKJj0x6dqCvrBnYtU/view?usp=drivesdk) | 220,537 |
| `results/run1_20261117/probes.jsonl` | [Drive](https://drive.google.com/file/d/1W-ufFkavAO0epXPtzBXJJVjmjQwpPcbw/view?usp=drivesdk) | 2,979 |
| `results/run1_20261117/q_graded.jsonl` | [Drive](https://drive.google.com/file/d/1aLiFF5ApZRhUxnLfuXyXTdAuAwP6UsBH/view?usp=drivesdk) | 26,989 |
| `results/run1_20261117/raw.jsonl.gz` | [Drive](https://drive.google.com/file/d/1UtfkV1p-dgbcN20yEAxa1kiYhm4ofABt/view?usp=drivesdk) | 8,680,496 |
| `results/smoke_test_20261117/deepseek_재시험/endpoints.jsonl` | [Drive](https://drive.google.com/file/d/1QZLkXOEOwrf2wCGcwXYrxgWwNnOW0qr0/view?usp=drivesdk) | 572 |
| `results/smoke_test_20261117/deepseek_재시험/raw.jsonl` | [Drive](https://drive.google.com/file/d/1l7NUa7UAE91hvqLxynCFOIfVyD0lXZ57/view?usp=drivesdk) | 1,515 |
| `results/smoke_test_20261117/endpoints.jsonl` | [Drive](https://drive.google.com/file/d/1S9szlbh8wPFODnDl4yn9e7m8Oc1bS8HQ/view?usp=drivesdk) | 26,259 |
| `results/smoke_test_20261117/mango_models_20261117.json` | [Drive](https://drive.google.com/file/d/1xYOp62coBlumm9h3q_bCbkkHgdjaQKL_/view?usp=drivesdk) | 3,707 |
| `results/smoke_test_20261117/mango_재시험/endpoints.jsonl` | [Drive](https://drive.google.com/file/d/1FYUrfu-9Rzi2SgU9szMMWDnkGyylt7UK/view?usp=drivesdk) | 0 |
| `results/smoke_test_20261117/mango_재시험/raw.jsonl` | [Drive](https://drive.google.com/file/d/19CYWXQzauSJ-eJ4u1YVHkSNCeiXuQjEp/view?usp=drivesdk) | 6,833 |
| `results/smoke_test_20261117/raw.jsonl` | [Drive](https://drive.google.com/file/d/1v5nBd9s56JVB3KxY777ETccyoZvDJu4x/view?usp=drivesdk) | 73,437 |
| `slides/01_추론서비스_경쟁력검토.pptx` | [Drive](https://drive.google.com/file/d/1jL0s8nuANOFRPf61_qCBCePHwfRzeiSS/view?usp=drivesdk) | 3,898,077 |
| `slides/03_OpenRouter_선택정책_지도.pptx` | [Drive](https://drive.google.com/file/d/1oTnhWknOFImarP1yP17rDsTQ0Y0erVY4/view?usp=drivesdk) | 31,828 |
| `slides/시간지역_성능_대시보드.html` | [Drive](https://drive.google.com/file/d/1IQx14rWnMmHegCraLTzxMXjFoS2hZCOv/view?usp=drivesdk) | 1,871,179 |
