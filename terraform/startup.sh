#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# AlphaDesk GCP startup script
# ---------------------------------------------------------------------------

# Mount persistent disk for the ledger + caches so data survives VM restarts
if ! mountpoint -q /opt/alphadesk-data 2>/dev/null; then
  mkdir -p /opt/alphadesk-data
  mkfs.ext4 -F /dev/disk/by-id/google-alphadesk-data 2>/dev/null || true
  mount -o discard,defaults /dev/disk/by-id/google-alphadesk-data /opt/alphadesk-data
  echo '/dev/disk/by-id/google-alphadesk-data /opt/alphadesk-data ext4 discard,defaults 0 2' >> /etc/fstab
fi
mkdir -p /opt/alphadesk-data
chown -R root:root /opt/alphadesk-data

# Install system deps
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

# Write .env with secrets from Terraform
cat > "$REPO_DIR/.env" <<EOF
ALPACA_API_KEY=${alpaca_key}
ALPACA_SECRET_KEY=${alpaca_secret}
ALPACA_PAPER=true
POLYGON_API_KEY=${polygon_key}
ADMIN_USERNAME=${admin_username}
ADMIN_PASSWORD=${admin_password}
MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=${ds_api_key}
DEEPSEEK_MODEL_SONNET=deepseek-v4-flash
DEEPSEEK_MODEL_HAIKU=deepseek-v4-flash
DEEPSEEK_MODEL_OPUS=deepseek-v4-pro
AUTORUN_START_ET=04:00
AUTORUN_END_ET=19:00
ALPHADESK_DATA=/opt/alphadesk-data
DASHBOARD_HOST=0.0.0.0
EOF

# Install Python deps
cd "$REPO_DIR"
pip install -r requirements.txt

# Systemd unit
cat > /etc/systemd/system/alphadesk.service <<'UNIT'
[Unit]
Description=AlphaDesk research engine
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/alphadesk
ExecStart=/usr/bin/python3 -m alphadesk.main dashboard
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable alphadesk
systemctl restart alphadesk

echo "AlphaDesk startup complete — dashboard at http://${alphadesk_ip}:8000"
