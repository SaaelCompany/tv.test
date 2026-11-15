#!/usr/bin/env bash
set -euo pipefail
echo "Удаляю всё, что создал install.sh (кроме PostgreSQL и пакетов)"
systemctl disable --now vpn-shop 2>/dev/null || true
rm -f /etc/systemd/system/vpn-shop.service
systemctl daemon-reload
rm -rf /opt/vpn-shop/{.venv,.env,backups}
sudo -u vpnshop crontab -r 2>/dev/null || true
echo "Готово. Код в /opt/vpn-shop остался."
echo "Чтобы удалить полностью: rm -rf /opt/vpn-shop /etc/caddy/Caddyfile"