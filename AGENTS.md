# 작업 시작

GitHub 작업 전에 학교 계정 `qonehan@snu.ac.kr` Drive의 [공통 규칙(GITHUB_CLOUD_SYSTEM.md)](https://drive.google.com/file/d/1KNLYcYaBB8FmGaUEuskLlHzwj40yJHMU/view)을 먼저 읽는다. 그다음 [자료 안내](ASSETS.md)와 [자료 목록](assets.manifest.json)을 읽는다.

- 원본(PDF·Office·생성 대시보드·측정 원시 데이터)은 Drive `github/mango_inference_test/<상대경로>`에 두고 Git에서 제외한다. 도구: `tools/cloud_assets.py` (upload·sync·pull·cache·check).
- pull은 `python -I tools/cloud_assets.py pull`(또는 `git cloud-pull`), 복원만은 `sync`. 로컬 수정본은 덮어쓰지 않는다.
- push 전 새 파일을 역할로 분류하고, Drive 대상은 `upload`로 올려 재다운로드 SHA-256 검증 후 manifest·ASSETS.md·`.gitignore`를 갱신한다. 커밋 전 `check`를 실행한다.
- 측정 원본은 Git blob(LF) 바이트 그대로 올린다(`results/*/SHA256SUMS`와 일치). 인증정보·토큰은 Git에 넣지 않는다.
