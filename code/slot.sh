#!/usr/bin/env bash
# 본 측정 회차 1회 (cron에서 호출). 사용: N=<회차 번호 1~4> ./slot.sh
# crontab 예 (UTC): 0 18 * * * cd /opt/mango-bench/code && RUN_ID=day1 N=1 ./slot.sh >> ../runs/cron.log 2>&1
set -euo pipefail
N=${N:?회차 번호}
PY=/opt/mango-bench/venv/bin/python
R=/opt/mango-bench/runs/${RUN_ID:?측정일 ID (예: day1)}/S$N  # 자정을 넘는 회차도 한 폴더에
$PY probe.py start --config config/day1.yaml --out "$R"
$PY runner.py --config config/day1.yaml --prompts ../prompts --slot "$N" --out "$R"
sleep 60
$PY fetch_generations.py "$R"
$PY check_slot.py "$R" --broadcast ../broadcast
