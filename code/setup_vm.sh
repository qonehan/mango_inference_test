#!/usr/bin/env bash
# 측정 VM 설정 (Ubuntu 24.04 LTS, 서울 리전). 프로토콜 부록 A-1.
# 사용: sudo DOMAIN=bench.example.com BROADCAST_TOKEN=<임의 문자열> bash setup_vm.sh <저장소 URL> <커밋>
#   DOMAIN: Broadcast 수신용 HTTPS 주소(이 VM 공인 IP를 가리키는 DNS 이름). 없으면 Broadcast 수신을 건너뜀.
# 키는 이 스크립트가 받지 않는다. 설치 후 사용자가 /opt/mango-bench/.env(권한 600)에 직접 넣는다:
#   MANGO_API_KEY=...  OPENROUTER_API_KEY=...  (파일럿 Broadcast 대조용) OPENROUTER_API_KEY_NOBC=...
set -euo pipefail
REPO=${1:?저장소 URL}
COMMIT=${2:?커밋 해시}
ROOT=/opt/mango-bench

apt-get update
apt-get install -y python3.12-venv git chrony traceroute whois jq curl
systemctl enable --now chrony

mkdir -p "$ROOT"/{runs,broadcast,prompts}
git clone "$REPO" "$ROOT/repo"
git -C "$ROOT/repo" checkout "$COMMIT"
ln -sfn "$ROOT/repo/code" "$ROOT/code"
git clone --depth 1 --branch v3.12.7 https://github.com/python/cpython.git "$ROOT/cpython"

python3.12 -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install --upgrade pip
"$ROOT/venv/bin/pip" install -r "$ROOT/code/requirements.lock"
sha256sum "$ROOT/code/requirements.lock" > "$ROOT/requirements.lock.sha256"

touch "$ROOT/.env" && chmod 600 "$ROOT/.env"
SUDO_USER_NAME=${SUDO_USER:-ubuntu}
chown -R "$SUDO_USER_NAME":"$SUDO_USER_NAME" "$ROOT"

# 질문 세트 생성 (o200k_base 토큰 기준, CPython v3.12.7) — 해시는 prompts/manifest.json
sudo -u "$SUDO_USER_NAME" bash -c "cd $ROOT/code && $ROOT/venv/bin/python build_prompts.py --cpython $ROOT/cpython --out $ROOT/prompts"

# Broadcast 수신기: 127.0.0.1:8787 + Caddy 자동 TLS(443)
if [[ -n "${DOMAIN:-}" ]]; then
  apt-get install -y caddy
  cat > /etc/systemd/system/bench-webhook.service <<EOF
[Unit]
Description=OpenRouter Broadcast receiver
After=network.target
[Service]
User=$SUDO_USER_NAME
Environment=BROADCAST_TOKEN=${BROADCAST_TOKEN:-}
ExecStart=$ROOT/venv/bin/python $ROOT/code/webhook.py --port 8787 --out $ROOT/broadcast
Restart=always
[Install]
WantedBy=multi-user.target
EOF
  cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
  reverse_proxy /broadcast* 127.0.0.1:8787
}
EOF
  systemctl daemon-reload
  systemctl enable --now bench-webhook
  systemctl restart caddy
  echo "Broadcast 주소: https://$DOMAIN/broadcast  (헤더 X-Bench-Token 설정)"
fi

echo "완료. 다음: /opt/mango-bench/.env 에 키 입력 → code/RUN.md의 파일럿 절차"
