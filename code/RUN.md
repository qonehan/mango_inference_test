# 측정 코드 실행 절차

프로토콜: [docs/02_실험_프로토콜.md](../docs/02_실험_프로토콜.md) (v0.9.4). 아래 순서대로 실행합니다. 경로는 VM 기준이며, `code`는 저장소로 가는 심볼릭 링크라 상대 경로(`../`) 대신 아래 변수를 씁니다.

```bash
ROOT=/opt/mango-bench; PY=$ROOT/venv/bin/python; cd $ROOT/code
```

## 파일 구성

| 파일 | 역할 |
|---|---|
| `config/day1.yaml`, `config/pilot.yaml` | 모델 ID, 고정 엔드포인트, 샘플링, 회차당 건수, 한도 |
| `bench_common.py` | 설정·키, 경로(arm), 속도 제한, 스트리밍 호출과 기록(조각 시각, TTFT, 연결 단계, 실패 분류, 요청 전체 마감) |
| `build_prompts.py` | W1·W3 풀, W2 세션(본 48 + 파일럿 6, 접두부 검사), L·Z, 도구 정의 생성과 해시 |
| `runner.py` | 회차 1회 실행 (주 비교 블록, 탐색 요청, 보조 레인, 부하 조건, 계정 한도 429 재전송, 회차 마감) |
| `probe.py` | 회차 시작 기록(환경, 시각 오차, IP·ASN·traceroute, 연결 10회, 모델·엔드포인트 스냅샷), 1분 대조군 |
| `fetch_generations.py` | OpenRouter generation 조회와 응답 엔드포인트 식별 |
| `check_slot.py` | 회차 점검(실패율, 결측, 결합률, 지출)과 파일 해시 봉인 |
| `webhook.py` | Broadcast 수신기 |
| `analyze.py` | 주 비교 판정(P1~P4), 기본 배분(회차 부트스트랩), 파일럿 W2 세션 수 산정 |
| `q_bench.py` | 품질 점검 문항 생성·실행·채점 |
| `broadcast_ab.py` | 파일럿 Broadcast 켬·끔 대조 (키 두 개) |
| `mock_server.py`, `verify_meter.py` | 측정기 검증 (실제 서비스 호출 없음) |
| `setup_vm.sh`, `requirements.lock` | VM 설정, 패키지 고정 |

## 1. 측정기 검증 (VM, 무료)

```bash
cd /opt/mango-bench/code
$PY verify_meter.py --out $ROOT/runs/meter_check.json          # 빠른 확인 (마감 5초)
$PY verify_meter.py --out $ROOT/runs/meter_check_full.json --full   # 실제 마감 300초
$PY -m unittest discover -s . -p "test_*.py"
```
합격: 동시 1·16 모두 중앙값 < 5ms, 마감 시험 pass.

## 2. 파일럿

```bash
R=$ROOT/runs/pilot
$PY probe.py start --config config/pilot.yaml --out $R
$PY runner.py --config config/pilot.yaml --prompts $ROOT/prompts --slot P --out $R --deadline-utc <ISO 시각>
sleep 60 && $PY fetch_generations.py $R
$PY check_slot.py $R --broadcast $ROOT/broadcast
$PY broadcast_ab.py --config config/pilot.yaml --prompts $ROOT/prompts --out $R/broadcast_ab
$PY q_bench.py run --config config/day1.yaml --q $ROOT/q --out $R/q --limit 20 && $PY q_bench.py grade $R/q --q $ROOT/q
$PY analyze.py $R --pilot --out $R/analysis
```
파일럿 뒤: 모델별 W2 세션 수 n(→ `w2_sessions_per_slot` = n/4), 회차 소요 시간, 잘림 비율(1% 초과면 `max_tokens_cap` 상향), Q 출력 길이로 예산 재계산 → 프로토콜 v1.0 동결(git 태그).

## 3. 본 측정 (회차마다, cron)

```bash
R=$ROOT/runs/$RUN_ID/S$N
$PY probe.py start --config config/day1.yaml --out $R
$PY runner.py --config config/day1.yaml --prompts $ROOT/prompts --slot $N --out $R
sleep 60 && $PY fetch_generations.py $R && $PY check_slot.py $R --broadcast $ROOT/broadcast
```
cron 예 (UTC 18·00·06·12시 = KST 03·09·15·21시): `0 18 * * * RUN_ID=day1 N=1 /opt/mango-bench/code/slot.sh` 형식으로 측정일 ID와 회차 번호를 넘깁니다(자정을 넘는 회차도 한 폴더에 모임).

## 4. 분석

```bash
$PY analyze.py $ROOT/runs/<날짜>/S1 $ROOT/runs/<날짜>/S2 $ROOT/runs/<날짜>/S3 $ROOT/runs/<날짜>/S4 --out $ROOT/runs/<날짜>/analysis
```

## 주의

- 실제 호출은 과금됩니다. 키는 `.env`(권한 600)에만 두고 출력·기록하지 않습니다.
- Q 문항 원문(`q_items.jsonl`)은 공개 저장소에 올리지 않습니다. ID·해시만 기록합니다.
- 모의 서버로는 측정기만 검증합니다. 모의 결과 수치는 의미가 없습니다.
