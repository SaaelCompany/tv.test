#!/usr/bin/env bash
# Запуск панели 3x-ui и магазина.
# Открывает порты панели, если их закрыл firewall установщика.
#
#   sudo bash start.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/vpn-shop}"
SERVICE="${SERVICE:-vpn-shop}"
ENV_FILE="${APP_DIR}/.env"

if [ "$(id -u)" -ne 0 ]; then
  echo "запустите от root: sudo bash $0" >&2
  exit 1
fi

read_env() {
  local key="$1"
  [ -f "$ENV_FILE" ] || return 0
  grep "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true
}

port_of() {
  local u="${1#*://}"
  u="${u%%/*}"
  if [[ "$u" == *:* ]]; then
    echo "${u##*:}"
  elif [[ "$1" == https://* ]]; then
    echo 443
  else
    echo 80
  fi
}

XUI_URL_VAL="$(read_env XUI_URL)"
SUB_URL_VAL="$(read_env XUI_SUB_BASE_URL)"
XUI_URL_VAL="${XUI_URL_VAL:-https://179.254.168.161.nip.io:2753/tele2}"
SUB_URL_VAL="${SUB_URL_VAL:-https://179.254.168.161.nip.io:2096/tele2}"
PANEL_PORT="$(port_of "$XUI_URL_VAL")"
SUB_PORT="$(port_of "$SUB_URL_VAL")"

echo "→ порты панели ${PANEL_PORT} и подписок ${SUB_PORT}"
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp >/dev/null || true
  ufw allow "${PANEL_PORT}/tcp" || true
  ufw allow "${SUB_PORT}/tcp" || true
  ufw allow 8000/tcp >/dev/null || true
fi

echo "→ панель 3x-ui"
if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^x-ui\.service'; then
  systemctl enable x-ui >/dev/null 2>&1 || true
  systemctl restart x-ui
  systemctl --no-pager --full status x-ui | head -n 12 || true
elif command -v x-ui >/dev/null 2>&1; then
  x-ui start || true
else
  echo "служба x-ui не найдена. Панель нужно запустить её собственной командой." >&2
fi

echo "→ магазин ${SERVICE}"
if [ ! -f "/etc/systemd/system/${SERVICE}.service" ]; then
  echo "сервис ${SERVICE} не установлен. Сначала: sudo bash install.sh" >&2
  exit 1
fi
systemctl enable "$SERVICE" >/dev/null 2>&1 || true
systemctl restart "$SERVICE"
sleep 2
if systemctl is-active --quiet "$SERVICE"; then
  echo "✓ ${SERVICE} запущен"
  echo "  панель:  ${XUI_URL_VAL}"
  echo "  ключи:   ${SUB_URL_VAL}"
else
  journalctl -u "$SERVICE" -n 40 --no-pager || true
  echo "✗ ${SERVICE} не запустился" >&2
  exit 1
fi
