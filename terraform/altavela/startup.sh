#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Altavela GCP startup script
# ---------------------------------------------------------------------------

# Mount persistent disk
if ! mountpoint -q /opt/altavela-data 2>/dev/null; then
  mkdir -p /opt/altavela-data
  mkfs.ext4 -F /dev/disk/by-id/google-altavela-data 2>/dev/null || true
  mount -o discard,defaults /dev/disk/by-id/google-altavela-data /opt/altavela-data
  echo '/dev/disk/by-id/google-altavela-data /opt/altavela-data ext4 discard,defaults 0 2' >> /etc/fstab
fi
mkdir -p /opt/altavela-data

# Install deps
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git

# Clone / pull
REPO_DIR=/opt/alphadesk
if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR" && git pull origin main
else
  git clone https://github.com/vigneshv1cky/AlphaDesk.git "$REPO_DIR"
fi

# Write .env
cat > "$REPO_DIR/altavela.env" <<EOF
MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=${ds_api_key}
DEEPSEEK_MODEL_SONNET=deepseek-v4-flash
DEEPSEEK_MODEL_HAIKU=deepseek-v4-flash
DEEPSEEK_MODEL_OPUS=deepseek-v4-pro
ALTAVELA_DATA=/opt/altavela-data
ADMIN_USERNAME=${admin_username}
ADMIN_PASSWORD=${admin_password}
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8001
AUTORUN_START_ET=00:00
AUTORUN_END_ET=23:59
AUTORUN_INTERVAL_HOURS=0.25
REPICK_COOLDOWN_HOURS=6
REPICK_MIN_PRICE_MOVE_PCT=5
EOF

# Install Python deps
cd "$REPO_DIR"
pip install -r requirements.txt

# Systemd unit
cat > /etc/systemd/system/altavela.service <<'UNIT'
[Unit]
Description=Altavela prediction-market engine
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/alphadesk
Environment=ALTAVELA_ENV=/opt/alphadesk/altavela.env
ExecStart=/usr/bin/python3 -m altavela.main dashboard
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable altavela
systemctl restart altavela

echo "Altavela startup complete — dashboard at http://${altavela_ip}:8001"
