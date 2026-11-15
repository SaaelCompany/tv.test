#!/usr/bin/env bash
#
# Полная установка VPN Shop на Ubuntu 22.04 / 24.04.
#
# Использование:
#   sudo DOMAIN=vpn.example.com XUI_API_TOKEN=xxx bash scripts/install.sh
#
# Флаги (через переменные окружения):
#   DOMAIN              — публичный домен сайта (обязателен)
#   SUB_DOMAIN          — публичный домен подписок (по умолчанию sub.$DOMAIN)
#   XUI_API_TOKEN       — токен 3x-ui (обязателен)
#   XUI_URL             — URL 3x-ui (по умолчанию http://127.0.0.1:2053)
#   XUI_INBOUND_ID      — ID inbound'а (по умолчанию 1)
#   XUI_SUB_BASE_URL    — база ссылок подписки (по умолчанию https://$SUB_DOMAIN)
#   ADMIN_PASSWORD      — пароль /admin (генерируется, если не задан)
#   SKIP_POSTGRES=1     — не трогать PostgreSQL
#   SKIP_CADDY=1        — не настраивать Caddy
#   SKIP_UFW=1          — не трогать firewall
#   SKIP_XUI_CHECK=1    — не проверять связь с 3x-ui
#   DRY_RUN=1           — только показать, ничего не менять
#
set -euo pipefail

# ──────────── Цвета ────────────
if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YLW=$'\033[33m'; C_BLU=$'\033[34m'; C_RST=$'\033[0m'
else
  C_RED=""; C_GRN=""; C_YLW=""; C_BLU=""; C_RST=""
fi
log()  { echo "${C_BLU}[install]${C_RST} $*"; }
ok()   { echo "${C_GRN}[ok]${C_RST} $*"; }
warn() { echo "${C_YLW}[warn]${C_RST} $*"; }
die()  { echo "${C_RED}[error]${C_RST} $*" >&2; exit 1; }

run() {
  if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "  [dry] $*"
  else
    eval "$@"
  fi
}

# ──────────── Проверки ────────────
[ "$(id -u)" -eq 0 ] || die "запустите от root: sudo bash $0"
[ -f /etc/os-release ] || die "не могу определить ОС"
. /etc/os-release
case "${ID:-}${VERSION_ID:-}" in
  ubuntu22.04|ubuntu24.04|debian12*) : ;;
  *) warn "ОС ${ID} ${VERSION_ID} не тестировалась — продолжаем на свой риск" ;;
esac

: "${DOMAIN:?DOMAIN не задан (пример: DOMAIN=vpn.example.com)}"
: "${XUI_API_TOKEN:?XUI_API_TOKEN не задан}"

SUB_DOMAIN="${SUB_DOMAIN:-sub.${DOMAIN}}"
XUI_URL="${XUI_URL:-http://127.0.0.1:2053}"
XUI_INBOUND_ID="${XUI_INBOUND_ID:-1}"
XUI_SUB_BASE_URL="${XUI_SUB_BASE_URL:-https://${SUB_DOMAIN}}"

APP_DIR="/opt/vpn-shop"
APP_USER="vpnshop"
DB_NAME="vpn"
DB_USER="vpn"
SERVICE="vpn-shop"

if [ ! -f "$APP_DIR/requirements.txt" ]; then
  die "$APP_DIR/requirements.txt не найден — сначала залейте код в $APP_DIR"
fi

log "Домен:          $DOMAIN"
log "Sub-домен:      $SUB_DOMAIN"
log "3x-ui:          $XUI_URL (inbound $XUI_INBOUND_ID)"
log "База:           PostgreSQL, db=$DB_NAME user=$DB_USER"
log "Приложение:     $APP_DIR, юзер $APP_USER"
[ "${DRY_RUN:-0}" = "1" ] && warn "DRY_RUN=1 — реальных изменений не будет"
echo

# ──────────── 1. Пакеты ────────────
log "→ устанавливаю пакеты"
run "export DEBIAN_FRONTEND=noninteractive"
run "apt-get update -qq"
run "apt-get install -y -qq \
    python3 python3-venv python3-pip \
    postgresql postgresql-contrib \
    git ufw fail2ban curl gnupg ca-certificates lsb-release \
    debian-keyring debian-archive-keyring apt-transport-https"

# Caddy
if ! command -v caddy >/dev/null 2>&1; then
  log "→ устанавливаю Caddy"
  run "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"
  run "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list"
  run "apt-get update -qq"
  run "apt-get install -y -qq caddy"
else
  ok "Caddy уже установлен: $(caddy version 2>/dev/null | head -1)"
fi
ok "пакеты установлены"

# ──────────── 2. Пользователь и папка ────────────
log "→ настраиваю пользователя $APP_USER"
if ! id "$APP_USER" >/dev/null 2>&1; then
  run "useradd -m -s /bin/bash $APP_USER"
fi
run "chown -R $APP_USER:$APP_USER $APP_DIR"
ok "пользователь готов"

# ──────────── 3. Python venv ────────────
log "→ готовлю venv"
if [ ! -d "$APP_DIR/.venv" ]; then
  run "sudo -u $APP_USER python3 -m venv $APP_DIR/.venv"
fi
run "sudo -u $APP_USER $APP_DIR/.venv/bin/pip install --quiet --upgrade pip"
run "sudo -u $APP_USER $APP_DIR/.venv/bin/pip install --quiet -r $APP_DIR/requirements.txt"
ok "venv готов"

# ──────────── 4. PostgreSQL ────────────
if [ "${SKIP_POSTGRES:-0}" != "1" ]; then
  log "→ настраиваю PostgreSQL"
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
    DB_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
    run "sudo -u postgres psql -c \"CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';\""
    ok "пользователь БД создан"
  else
    # Уже существует — берём пароль из .env или генерируем новый
    if [ -f "$APP_DIR/.env" ]; then
      DB_PASSWORD=$(grep '^DATABASE_URL' "$APP_DIR/.env" | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')
    fi
    if [ -z "${DB_PASSWORD:-}" ]; then
      DB_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
      run "sudo -u postgres psql -c \"ALTER USER $DB_USER WITH PASSWORD '$DB_PASSWORD';\""
      warn "пароль существующего пользователя БД сброшен"
    fi
  fi

  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
    run "sudo -u postgres psql -c \"CREATE DATABASE $DB_NAME OWNER $DB_USER;\""
    ok "база $DB_NAME создана"
  else
    ok "база $DB_NAME уже существует"
  fi

  run "systemctl enable --now postgresql"
  # Проверка подключения
  if [ "${DRY_RUN:-0}" != "1" ]; then
    if PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -c 'SELECT 1' >/dev/null 2>&1; then
      ok "подключение к PostgreSQL работает"
    else
      die "не могу подключиться к PostgreSQL — проверьте /etc/postgresql/*/main/pg_hba.conf"
    fi
  fi
else
  warn "SKIP_POSTGRES=1 — пропускаю PostgreSQL"
  DB_PASSWORD="${DB_PASSWORD:-}"
fi

# ──────────── 5. .env ────────────
log "→ готовлю .env"
if [ -f "$APP_DIR/.env" ]; then
  warn "$APP_DIR/.env уже существует — не перезаписываю. Проверьте вручную."
  warn "Если хотите сгенерировать заново: mv $APP_DIR/.env $APP_DIR/.env.bak"
else
  SECRET_KEY=$(openssl rand -urlsafe 48 2>/dev/null || openssl rand -hex 32)
  ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)}"
  DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}"

  if [ "${DRY_RUN:-0}" != "1" ]; then
    cat > "$APP_DIR/.env" <<EOF
# ════════ Режимы ════════
DEBUG=false
XUI_MOCK_MODE=false
ROBOKASSA_MOCK=true

# ════════ 3x-ui ════════
XUI_URL=${XUI_URL}
XUI_API_TOKEN=${XUI_API_TOKEN}
XUI_INBOUND_ID=${XUI_INBOUND_ID}
XUI_SUB_BASE_URL=${XUI_SUB_BASE_URL}

# ════════ Robokassa (пока mock) ════════
ROBOKASSA_LOGIN=
ROBOKASSA_PASSWORD1=
ROBOKASSA_PASSWORD2=
ROBOKASSA_TEST_MODE=true
ROBOKASSA_HASH_TYPE=md5
ROBOKASSA_FISCAL_MODE=false

# ════════ Сайт ════════
BASE_URL=https://${DOMAIN}
SECRET_KEY=${SECRET_KEY}
COOKIE_SECURE=true
COOKIE_DOMAIN=${DOMAIN}
TRUSTED_HOSTS=${DOMAIN},${SUB_DOMAIN},localhost,127.0.0.1
ADMIN_PASSWORD=${ADMIN_PASSWORD}

# ════════ БД ════════
DATABASE_URL=${DATABASE_URL}

# ════════ SMTP (опционально) ════════
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
EOF
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    ok ".env создан ($APP_DIR/.env)"
  else
    echo "  [dry] сгенерировал бы $APP_DIR/.env"
  fi
fi

# ──────────── 6. systemd ────────────
log "→ настраиваю systemd-юнит"
if [ "${DRY_RUN:-0}" != "1" ]; then
  cat > /etc/systemd/system/${SERVICE}.service <<EOF
[Unit]
Description=VPN Shop (FastAPI)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/gunicorn app.main:app \\
    -k uvicorn.workers.UvicornWorker \\
    -w 2 \\
    -b 127.0.0.1:8000 \\
    --access-logfile - \\
    --error-logfile - \\
    --timeout 60 \\
    --graceful-timeout 30
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${APP_DIR}
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$SERVICE"
  systemctl restart "$SERVICE"
  sleep 3
  if systemctl is-active --quiet "$SERVICE"; then
    ok "$SERVICE запущен"
  else
    warn "сервис не поднялся — смотрите journalctl -u $SERVICE -n 50"
  fi
else
  echo "  [dry] создал бы /etc/systemd/system/${SERVICE}.service"
fi

# ──────────── 7. Caddy ────────────
if [ "${SKIP_CADDY:-0}" != "1" ]; then
  log "→ настраиваю Caddy"
  if [ "${DRY_RUN:-0}" != "1" ]; then
    cp -n /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak 2>/dev/null || true
    cat > /etc/caddy/Caddyfile <<EOF
${DOMAIN} {
    encode gzip
    reverse_proxy 127.0.0.1:8000
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
    }
}

${SUB_DOMAIN} {
    reverse_proxy 127.0.0.1:2096
}
EOF
    caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
      && ok "Caddyfile валиден" \
      || warn "Caddyfile не прошёл валидацию — проверьте вручную"
    systemctl reload caddy 2>/dev/null || systemctl restart caddy
    ok "Caddy перезагружен"
  else
    echo "  [dry] создал бы /etc/caddy/Caddyfile для ${DOMAIN}, ${SUB_DOMAIN}"
  fi
else
  warn "SKIP_CADDY=1 — пропускаю Caddy"
fi

# ──────────── 8. Firewall ────────────
if [ "${SKIP_UFW:-0}" != "1" ]; then
  log "→ настраиваю firewall"
  run "ufw --force default deny incoming"
  run "ufw --force default allow outgoing"
  run "ufw allow 22/tcp comment 'SSH'"
  run "ufw allow 80/tcp comment 'HTTP'"
  run "ufw allow 443/tcp comment 'HTTPS'"
  # Порт VPN, если inbound на 443 — уже открыт
  run "ufw --force enable"
  ok "firewall настроен"
  warn "если у вас inbound'ы на других портах (например 8443/tcp) — откройте: ufw allow 8443/tcp"
else
  warn "SKIP_UFW=1 — пропускаю firewall"
fi

# ──────────── 9. fail2ban ────────────
log "→ включаю fail2ban"
run "systemctl enable --now fail2ban"
ok "fail2ban активен"

# ──────────── 10. Бэкапы ────────────
log "→ настраиваю бэкап"
if [ "${DRY_RUN:-0}" != "1" ]; then
  mkdir -p "$APP_DIR/scripts" "$APP_DIR/backups"
  cat > "$APP_DIR/scripts/backup_db.sh" <<'BKP'
#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR=/opt/vpn-shop/backups
mkdir -p "$BACKUP_DIR"
STAMP=$(date +%F_%H%M)
PGPASSWORD=$(grep '^DATABASE_URL' /opt/vpn-shop/.env | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')
pg_dump -h 127.0.0.1 -U vpn vpn | gzip > "$BACKUP_DIR/vpn_${STAMP}.sql.gz"
find "$BACKUP_DIR" -name 'vpn_*.sql.gz' -mtime +14 -delete
BKP
  chmod +x "$APP_DIR/scripts/backup_db.sh"
  chown -R "$APP_USER:$APP_USER" "$APP_DIR/scripts" "$APP_DIR/backups"

  # Cron пользователя vpnshop
  CRON_LINE="0 3 * * * $APP_DIR/scripts/backup_db.sh"
  if ! sudo -u "$APP_USER" crontab -l 2>/dev/null | grep -qF "$APP_DIR/scripts/backup_db.sh"; then
    ( sudo -u "$APP_USER" crontab -l 2>/dev/null; echo "$CRON_LINE" ) | sudo -u "$APP_USER" crontab -
    ok "cron-бэкап добавлен (03:00 ежедневно)"
  else
    ok "cron-бэкап уже настроен"
  fi
fi

# ──────────── 11. Проверка 3x-ui ────────────
if [ "${SKIP_XUI_CHECK:-0}" != "1" ] && [ "${DRY_RUN:-0}" != "1" ]; then
  log "→ проверяю связь с 3x-ui"
  if curl -sf -m 5 -H "Authorization: Bearer ${XUI_API_TOKEN}" \
      "${XUI_URL}/panel/api/inbounds/get/${XUI_INBOUND_ID}" >/dev/null; then
    ok "3x-ui доступен, inbound $XUI_INBOUND_ID найден"
  else
    warn "не могу достучаться до ${XUI_URL}/panel/api/inbounds/get/${XUI_INBOUND_ID}"
    warn "проверьте XUI_API_TOKEN, XUI_URL, XUI_INBOUND_ID, и что 3x-ui запущен"
  fi
fi

# ──────────── 12. Health-проверка ────────────
if [ "${DRY_RUN:-0}" != "1" ]; then
  sleep 2
  if curl -sf -m 5 http://127.0.0.1:8000/healthz >/dev/null; then
    ok "app отвечает на /healthz"
  else
    warn "app не отвечает на /healthz — смотрите journalctl -u $SERVICE -n 50"
  fi
fi

# ──────────── Финал ────────────
echo
echo "════════════════════════════════════════════════════════════"
ok "Установка завершена"
echo "════════════════════════════════════════════════════════════"
echo
echo "  Сайт:            https://${DOMAIN}"
echo "  Админка:         https://${DOMAIN}/admin"
if [ -z "${ADMIN_PASSWORD:-}" ]; then
  echo "  ADMIN_PASSWORD:  см. $APP_DIR/.env"
else
  echo "  ADMIN_PASSWORD:  ${ADMIN_PASSWORD}"
fi
echo
echo "  Логи:            journalctl -u ${SERVICE} -f"
echo "  Перезапуск:      systemctl restart ${SERVICE}"
echo "  Статус:          systemctl status ${SERVICE}"
echo "  Проверка:        curl https://${DOMAIN}/healthz"
echo
echo "  ⚠  DNS: убедитесь, что A-записи ${DOMAIN} и ${SUB_DOMAIN}"
echo "     указывают на этот сервер, иначе Caddy не получит сертификат."
echo
echo "  ⚠  3x-ui панель должна слушать 127.0.0.1:2053 (не 0.0.0.0)."
echo "     Проверка: ss -tlnp | grep 2053"
echo