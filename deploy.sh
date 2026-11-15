cat > /root/deploy.sh <<'DEPLOY_EOF'
#!/usr/bin/env bash
# Установка VPN Shop на Ubuntu 22.04/24.04/26.04 по IP (без домена).
# Использование:
#   bash deploy.sh                          # спросит токен 3x-ui
#   XUI_TOKEN=xxx bash deploy.sh            # без вопросов
set -euo pipefail

APP_DIR=/opt/vpn-shop
APP_USER=vpnshop
DB_NAME=vpn
DB_USER=vpn
SERVICE=vpn-shop
PORT=8000

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_blu=$'\033[34m'; c_rst=$'\033[0m'
log(){ echo "${c_blu}==>${c_rst} $*"; }
ok(){ echo "${c_grn} ok ${c_rst} $*"; }
warn(){ echo "${c_ylw}warn${c_rst} $*"; }
die(){ echo "${c_red}fail${c_rst} $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "запустите от root"
[ -d "$APP_DIR/app" ] || die "$APP_DIR/app не найден — залейте код в $APP_DIR"

# ─── IP ───
SERVER_IP="${SERVER_IP:-$(curl -s4 --max-time 5 ifconfig.me || hostname -I | awk '{print $1}')}"
[ -n "$SERVER_IP" ] || die "не могу определить публичный IP, задайте SERVER_IP=..."

# ─── XUI_TOKEN ───
XUI_TOKEN="${XUI_TOKEN:-}"
if [ -z "$XUI_TOKEN" ]; then
  echo
  echo "Нужен API-токен 3x-ui: панель → Settings → Security → API Token → Generate"
  read -r -p "Вставьте токен: " XUI_TOKEN
fi
[ -n "$XUI_TOKEN" ] || die "токен обязателен"
XUI_INBOUND_ID="${XUI_INBOUND_ID:-1}"
XUI_URL="${XUI_URL:-http://127.0.0.1:2053}"

log "IP:        $SERVER_IP"
log "Порт:      $PORT"
log "3x-ui:     $XUI_URL (inbound $XUI_INBOUND_ID)"
log "Папка:     $APP_DIR"
echo

# ─── 1. Пакеты ───
log "→ пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip postgresql postgresql-contrib ufw fail2ban curl >/dev/null
ok "пакеты установлены"

# ─── 2. Пользователь ───
id "$APP_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$APP_USER"
ok "пользователь $APP_USER"

# ─── 3. Чистка мусора от прошлых попыток ───
log "→ чистка старых артефактов"
cd "$APP_DIR"
rm -f -- '#' '-r' '0.28.1' 'pip' 'python' 2>/dev/null || true
rm -f vpn_shop.db 2>/dev/null || true
rm -rf .venv 2>/dev/null || true
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
ok "папка очищена"

# ─── 4. Фикс requirements для Python 3.14 ───
log "→ зависимости"
[ -f requirements.txt ] || die "нет requirements.txt"

# psycopg-binary==3.2.3 не собирается на Python 3.14
sed -i 's/psycopg\[binary\]==3\.2\.3/psycopg[binary]>=3.2.10/' requirements.txt || true
# pydantic-core на 3.14 — только свежие версии
sed -i 's/pydantic==2\.9\.2/pydantic>=2.10/' requirements.txt || true

chown -R "$APP_USER:$APP_USER" "$APP_DIR"

sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
if ! sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"; then
  die "pip install упал — пришлите вывод выше"
fi
[ -x "$APP_DIR/.venv/bin/gunicorn" ] || die "gunicorn не установился"
ok "зависимости установлены"

# ─── 5. PostgreSQL ───
log "→ PostgreSQL"
systemctl enable --now postgresql >/dev/null 2>&1 || true

# пароль для пользователя БД (генерируем при первом запуске, сохраняем в .env)
if [ -f "$APP_DIR/.env" ] && grep -q '^DATABASE_URL=' "$APP_DIR/.env"; then
  DB_PASS=$(grep '^DATABASE_URL=' "$APP_DIR/.env" | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')
fi
if [ -z "${DB_PASS:-}" ]; then
  DB_PASS=$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)
fi

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" >/dev/null
  ok "пользователь БД создан"
else
  sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';" >/dev/null
  ok "пароль пользователя БД обновлён"
fi

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" >/dev/null
  ok "база $DB_NAME создана"
else
  ok "база $DB_NAME уже есть"
fi

PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -c 'SELECT 1' >/dev/null \
  || die "не подключаюсь к PostgreSQL — проверьте pg_hba.conf"
ok "PostgreSQL подключен"

# ─── 6. .env ───
log "→ .env"
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -base64 18 | tr -d '/+=' | head -c 16)}"

cat > "$APP_DIR/.env" <<ENV_EOF
DEBUG=false
SECRET_KEY=${SECRET_KEY}
BASE_URL=http://${SERVER_IP}:${PORT}
COOKIE_SECURE=false
COOKIE_DOMAIN=

DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}

XUI_MOCK_MODE=false
XUI_URL=${XUI_URL}
XUI_API_TOKEN=${XUI_TOKEN}
XUI_INBOUND_ID=${XUI_INBOUND_ID}
XUI_SUB_BASE_URL=http://${SERVER_IP}:2096
XUI_SYNC_INTERVAL=120

ROBOKASSA_MOCK=true
ROBOKASSA_LOGIN=
ROBOKASSA_PASSWORD1=
ROBOKASSA_PASSWORD2=
ROBOKASSA_TEST_MODE=true
ROBOKASSA_HASH_TYPE=md5

ADMIN_PASSWORD=${ADMIN_PASSWORD}
TRUSTED_HOSTS=${SERVER_IP},localhost,127.0.0.1

SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
ENV_EOF

chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"
ok ".env создан"

# ─── 7. Проверка конфига ───
log "→ проверка конфига"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && .venv/bin/python -c 'from app.config import settings; assert settings.database_url.startswith(\"postgresql\"); assert len(settings.secret_key)>=32; assert settings.xui_api_token; print(\"OK\")'" \
  || die "конфиг не прошёл проверку"
ok "конфиг валиден"

# ─── 8. Проверка 3x-ui ───
log "→ проверка 3x-ui"
if curl -sf -m 5 -H "Authorization: Bearer $XUI_TOKEN" \
   "${XUI_URL}/panel/api/inbounds/get/${XUI_INBOUND_ID}" >/dev/null; then
  ok "3x-ui доступен, inbound $XUI_INBOUND_ID найден"
else
  warn "3x-ui не отвечает — проверьте XUI_URL, токен и inbound_id"
  warn "продолжаем, но покупки не будут создавать клиентов"
fi

# ─── 9. systemd ───
log "→ systemd"
cat > /etc/systemd/system/${SERVICE}.service <<UNIT_EOF
[Unit]
Description=VPN Shop
After=network.target postgresql.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:${PORT} --access-logfile - --error-logfile -
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT_EOF

systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl restart "$SERVICE"
sleep 4
systemctl is-active --quiet "$SERVICE" || {
  journalctl -u "$SERVICE" -n 50 --no-pager
  die "$SERVICE не запустился"
}
ok "$SERVICE запущен"

# ─── 10. Firewall ───
log "→ firewall"
ufw --force default deny incoming >/dev/null
ufw --force default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw allow "${PORT}/tcp" >/dev/null
ufw --force enable >/dev/null
ok "открыты 22 и ${PORT}"

systemctl enable --now fail2ban >/dev/null 2>&1 || true
ok "fail2ban активен"

# ─── 11. Health ───
log "→ health"
sleep 2
if curl -sf -m 5 "http://127.0.0.1:${PORT}/healthz" >/dev/null; then
  ok "приложение отвечает"
else
  journalctl -u "$SERVICE" -n 40 --no-pager
  die "app не отвечает на /healthz"
fi

# ─── Готово ───
echo
echo "════════════════════════════════════════════════════════════"
echo "${c_grn} ГОТОВО ${c_rst}"
echo "════════════════════════════════════════════════════════════"
echo
echo "  Сайт:            http://${SERVER_IP}:${PORT}"
echo "  Админка:         http://${SERVER_IP}:${PORT}/admin"
echo "  ADMIN_PASSWORD:  ${ADMIN_PASSWORD}"
echo
echo "  Логи:      journalctl -u ${SERVICE} -f"
echo "  Рестарт:   systemctl restart ${SERVICE}"
echo "  .env:      ${APP_DIR}/.env"
echo
echo "  Сохраните ADMIN_PASSWORD — он только здесь и в .env"
echo
DEPLOY_EOF

chmod +x /root/deploy.sh