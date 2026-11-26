#!/bin/bash
# 실험 2-2회 예약 (2026-11-22). 사용: sudo bash schedule_20261122.sh seoul|iad
# 회차 r1~r4: 2026-11-22 05:30 · 11:30 · 17:30 · 23:30 UTC (6시간 간격, 두 VM 동시)
# 서울만: 공개 지표 수집(05:00부터 21시간), T·J(회차 사이 빈 시간, 이어 하기), 각 T·J 뒤 generation 회수
# 미국: 각 회차 45분 뒤 generation 회수
# 비용 상한(VM별 기록의 추정 합): 서울 $70, 미국 $20 → 합 $90 (설계 상한 $95 안)
set -e
VM=$1
D=/opt/mango-bench/exp2_2
PY=/opt/mango-bench/venv/bin/python
RUN="$PY $D/runner_20261122.py"
ANCH="deepseek-v4.1-flash-1017=deepseek@T,deepseek-v4.1-flash-1017=morph/fast@J,glm-5.3=alibaba/prime@T,glm-5.3=fireworks@J,glm-5.3-flash=parasail/fp8@T,glm-5.3-flash=relace/fp4@J,kimi-k3.1-pro=decart/mxfp4@T,kimi-k3.1-pro=deepinfra/bf16@J"
if [ "$VM" = seoul ]; then CAP=70; else CAP=20; fi

job() {  # job <unit> <"YYYY-MM-DD HH:MM:SS" 또는 now> <명령>
  local unit=$1 when=$2; shift 2
  local cal=()
  [ "$when" != now ] && cal=(--on-calendar="$when UTC" --timer-property=AccuracySec=1s)
  systemd-run --unit="$unit" "${cal[@]}" -p User=ubuntu -p WorkingDirectory=$D \
    -p EnvironmentFile=/opt/mango-bench/.env /bin/bash -c "$* >> $D/logs/$unit.log 2>&1"
}

i=1
for t in "2026-11-22 05:30:00" "2026-11-22 11:30:00" "2026-11-22 17:30:00" "2026-11-22 23:30:00"; do
  job exp22-r$i "$t" "$RUN round --vm $VM --round-id r$i --out $D/run --max-usd $CAP"
  i=$((i+1))
done

if [ "$VM" = seoul ]; then
  job exp22-collect now "$PY $D/collect_public_20261122.py collect --out $D/public --hours 21 --start-utc 2026-11-22T05:00"
  job exp22-tj1 "2026-11-22 06:20:00" "$RUN tj --out $D/run --max-usd $CAP --until-utc 11:20 --anchors $ANCH; $RUN gens --out $D/run"
  job exp22-tj2 "2026-11-22 12:20:00" "$RUN tj --out $D/run --max-usd $CAP --until-utc 17:20 --anchors $ANCH; $RUN gens --out $D/run"
  job exp22-tj3 "2026-11-22 18:20:00" "$RUN tj --out $D/run --max-usd $CAP --until-utc 23:20 --anchors $ANCH; $RUN gens --out $D/run"
  job exp22-tj4 "2026-11-23 00:20:00" "$RUN tj --out $D/run --max-usd $CAP --until-utc 06:00 --anchors $ANCH; $RUN gens --out $D/run"
else
  job exp22-g1 "2026-11-22 06:15:00" "$RUN gens --out $D/run"
  job exp22-g2 "2026-11-22 12:15:00" "$RUN gens --out $D/run"
  job exp22-g3 "2026-11-22 18:15:00" "$RUN gens --out $D/run"
  job exp22-g4 "2026-11-23 00:15:00" "$RUN gens --out $D/run"
fi
systemctl list-timers --all --no-pager | grep exp22 || true
systemctl is-active exp22-collect 2>/dev/null || true
