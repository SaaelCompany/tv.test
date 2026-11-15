#!/usr/bin/env bash
set -euo pipefail
APP_DIR="/opt/vpn-shop"
SERVICE="vpn-shop"

[ "$(id -u)" -eq 0 ] || { echo "sudo required"; exit 1; }
cd "$APP_DIR"

echo "→ git pull"
sudo -u vpnshop git pull --ff-only

echo "→ pip install"
sudo -u vpnshop .venv/bin/pip install --quiet -r requirements.txt

echo "→ restart $SERVICE"
systemctl restart "$SERVICE"
sleep 3

if systemctl is-active --quiet "$SERVICE"; then
  echo "✓ $SERVICE запущен"
  journalctl -u "$SERVICE" -n 15 --no-pager
else
  echo "✗ $SERVICE упал — смотрите journalctl -u $SERVICE -n 50"
  exit 1
fi