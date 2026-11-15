#!/usr/bin/env bash
# Автоустановка магазина подписок.
#
# Из каталога с кодом, от root:
#   sudo bash install.sh
#   sudo XUI_API_TOKEN=xxx bash install.sh
#   sudo DOMAIN=ai.example.com XUI_API_TOKEN=xxx bash install.sh
#
# Без DOMAIN сайт открывается по IP на порту PORT (по умолчанию 8000).
# С DOMAIN ставится Caddy и HTTPS.
#
# Повторный запуск обновляет код и зависимости и не затирает .env,
# если не задан RECONFIGURE=1.
#
# Прочее:
#   XUI_TOKEN          — то же, что XUI_API_TOKEN
#   XUI_URL            — панель, по умолчанию https://179.254.168.161.nip.io:2753/tele2
#   XUI_INBOUND_ID     — по умолчанию 1
#   XUI_SUB_BASE_URL   — база ссылок ключа, по умолчанию https://179.254.168.161.nip.io:2096/tele2
#   XUI_SYNC_INTERVAL  — секунды, по умолчанию 120
#   ADMIN_PASSWORD     — иначе генерируется
#   PORT               — порт без домена, по умолчанию 8000
#   SKIP_POSTGRES=1 SKIP_CADDY=1 SKIP_UFW=1 SKIP_XUI_CHECK=1
#   DRY_RUN=1          — только план, без изменений
set -euo pipefail

if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YLW=$'\033[33m'; C_BLU=$'\033[34m'; C_RST=$'\033[0m'
else
  C_RED=""; C_GRN=""; C_YLW=""; C_BLU=""; C_RST=""
fi
log()  { echo "${C_BLU}[install]${C_RST} $*"; }
ok()   { echo "${C_GRN}[ok]${C_RST} $*"; }
warn() { echo "${C_YLW}[warn]${C_RST} $*"; }
die()  { echo "${C_RED}[error]${C_RST} $*" >&2; exit 1; }

DRY_RUN="${DRY_RUN:-0}"
run() {
  if [ "$DRY_RUN" = "1" ]; then
    echo "  [dry] $*"
  else
    eval "$@"
  fi
}

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-/opt/vpn-shop}"
APP_USER="${APP_USER:-vpnshop}"
DB_NAME="${DB_NAME:-vpn}"
DB_USER="${DB_USER:-vpn}"
SERVICE="${SERVICE:-vpn-shop}"
PORT="${PORT:-8000}"
XUI_API_TOKEN="${XUI_API_TOKEN:-${XUI_TOKEN:-}}"
XUI_URL="${XUI_URL:-https://179.254.168.161.nip.io:2753/tele2}"
XUI_SUB_BASE_URL="${XUI_SUB_BASE_URL:-https://179.254.168.161.nip.io:2096/tele2}"
XUI_INBOUND_ID="${XUI_INBOUND_ID:-1}"
XUI_SYNC_INTERVAL="${XUI_SYNC_INTERVAL:-120}"
DOMAIN="${DOMAIN:-}"
RECONFIGURE="${RECONFIGURE:-0}"

[ -f "$SOURCE_DIR/app/main.py" ] || die "рядом со скриптом нет app/main.py"
[ -f "$SOURCE_DIR/requirements.txt" ] || die "рядом со скриптом нет requirements.txt"

if [ "$DRY_RUN" != "1" ] && [ "$(id -u)" -ne 0 ]; then
  die "запустите от root: sudo bash $0"
fi

if [ -z "$XUI_API_TOKEN" ] && [ -t 0 ] && [ "$DRY_RUN" != "1" ]; then
  echo
  echo "Нужен API-токен панели: Settings → Security → API Token."
  read -r -p "Вставьте токен: " XUI_API_TOKEN
fi
[ -n "$XUI_API_TOKEN" ] || [ "$DRY_RUN" = "1" ] || die "задайте XUI_API_TOKEN (или XUI_TOKEN)"

if [ -z "$DOMAIN" ]; then
  SERVER_IP="${SERVER_IP:-}"
  if [ -z "$SERVER_IP" ]; then
    SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
  [ -n "$SERVER_IP" ] || SERVER_IP="127.0.0.1"
  BASE_URL="http://${SERVER_IP}:${PORT}"
  COOKIE_SECURE="false"
  BIND_ADDR="0.0.0.0:${PORT}"
  TRUSTED_HOSTS="${SERVER_IP},localhost,127.0.0.1"
  USE_CADDY=0
else
  SUB_DOMAIN="${SUB_DOMAIN:-sub.${DOMAIN}}"
  BASE_URL="https://${DOMAIN}"
  COOKIE_SECURE="true"
  BIND_ADDR="127.0.0.1:${PORT}"
  TRUSTED_HOSTS="${DOMAIN},${SUB_DOMAIN},localhost,127.0.0.1"
  USE_CADDY=1
fi
if [ "${SKIP_CADDY:-0}" = "1" ]; then
  USE_CADDY=0
fi

log "Код:       $SOURCE_DIR"
log "Куда:      $APP_DIR"
log "Сайт:      $BASE_URL"
log "Панель:    $XUI_URL (inbound $XUI_INBOUND_ID)"
log "Ключи:     $XUI_SUB_BASE_URL"
[ "$DRY_RUN" = "1" ] && warn "DRY_RUN=1 — изменений не будет"
echo

# ──────────── Пакеты ────────────
log "→ пакеты"
run "export DEBIAN_FRONTEND=noninteractive"
run "apt-get update -qq"
run "apt-get install -y -qq python3 python3-venv python3-pip postgresql postgresql-contrib rsync ufw curl ca-certificates openssl"
if [ "$USE_CADDY" = "1" ] && ! command -v caddy >/dev/null 2>&1; then
  log "→ Caddy"
  run "apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg"
  run "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"
  run "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list"
  run "apt-get update -qq"
  run "apt-get install -y -qq caddy"
fi
ok "пакеты готовы"

# ──────────── Код ────────────
log "→ копирую код в $APP_DIR"
if ! id "$APP_USER" >/dev/null 2>&1; then
  run "useradd -m -s /bin/bash $APP_USER"
fi
run "mkdir -p '$APP_DIR'"
if [ "$SOURCE_DIR" != "$APP_DIR" ]; then
  run "rsync -a --delete \
    --exclude '.git/' --exclude '.venv/' --exclude '.env' \
    --exclude '__pycache__/' --exclude '.pytest_cache/' \
    --exclude '*.db' --exclude 'backups/' \
    '$SOURCE_DIR/' '$APP_DIR/'"
fi
run "chown -R $APP_USER:$APP_USER '$APP_DIR'"
ok "код на месте"

# ──────────── Python ────────────
log "→ окружение Python"
PY_MINOR="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ "$DRY_RUN" != "1" ]; then
  case "$PY_MINOR" in
    3.14|3.15|3.16)
      sed -i 's/psycopg\[binary\]==3\.2\.3/psycopg[binary]>=3.2.10/' "$APP_DIR/requirements.txt" || true
      sed -i 's/pydantic==2\.9\.2/pydantic>=2.10/' "$APP_DIR/requirements.txt" || true
      ;;
  esac
  if [ ! -d "$APP_DIR/.venv" ]; then
    sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
  fi
  sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
  sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
  [ -x "$APP_DIR/.venv/bin/gunicorn" ] || die "gunicorn не установился"
fi
ok "зависимости установлены"

# ──────────── PostgreSQL ────────────
if [ "${SKIP_POSTGRES:-0}" != "1" ]; then
  log "→ PostgreSQL"
  run "systemctl enable --now postgresql"
  if [ "$DRY_RUN" != "1" ]; then
    if [ -f "$APP_DIR/.env" ] && grep -q '^DATABASE_URL=' "$APP_DIR/.env"; then
      DB_PASSWORD="$(grep '^DATABASE_URL=' "$APP_DIR/.env" | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')"
    fi
    if [ -z "${DB_PASSWORD:-}" ]; then
      DB_PASSWORD="$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"
    fi
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
      sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';" >/dev/null
      ok "пользователь БД создан"
    else
      sudo -u postgres psql -c "ALTER USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';" >/dev/null
      ok "пароль пользователя БД обновлён"
    fi
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
      sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};" >/dev/null
      ok "база ${DB_NAME} создана"
    else
      ok "база ${DB_NAME} уже есть"
    fi
    HBA="$(sudo -u postgres psql -tAc 'SHOW hba_file' | tr -d '[:space:]')"
    if [ -n "$HBA" ] && ! grep -qE '^host[[:space:]]+all[[:space:]]+all[[:space:]]+127\.0\.0\.1/32' "$HBA"; then
      echo "host all all 127.0.0.1/32 scram-sha-256" >> "$HBA"
      systemctl reload postgresql
    fi
    if ! PGPASSWORD="$DB_PASSWORD" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -c 'SELECT 1' >/dev/null 2>&1; then
      die "нет подключения к PostgreSQL. Проверьте $HBA"
    fi
    ok "PostgreSQL отвечает"
  fi
else
  warn "SKIP_POSTGRES=1"
  DB_PASSWORD="${DB_PASSWORD:-}"
fi

# ──────────── .env ────────────
log "→ .env"
if [ -f "$APP_DIR/.env" ] && [ "$RECONFIGURE" != "1" ]; then
  warn "$APP_DIR/.env уже есть — пароли не трогаю, адреса панели обновляю. Пересоздать целиком: RECONFIGURE=1"
  if [ -z "${ADMIN_PASSWORD:-}" ]; then
    ADMIN_PASSWORD="$(grep '^ADMIN_PASSWORD=' "$APP_DIR/.env" | head -1 | cut -d= -f2- || true)"
  fi
else
  SECRET_KEY="$(openssl rand -hex 32)"
  ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)}"
  if [ "$DRY_RUN" != "1" ]; then
    cat > "$APP_DIR/.env" <<EOF
DEBUG=false
XUI_MOCK_MODE=false
ROBOKASSA_MOCK=true

XUI_URL=${XUI_URL}
XUI_API_TOKEN=${XUI_API_TOKEN}
XUI_INBOUND_ID=${XUI_INBOUND_ID}
XUI_SUB_BASE_URL=${XUI_SUB_BASE_URL}
XUI_SYNC_INTERVAL=${XUI_SYNC_INTERVAL}

ROBOKASSA_LOGIN=
ROBOKASSA_PASSWORD1=
ROBOKASSA_PASSWORD2=
ROBOKASSA_TEST_MODE=true
ROBOKASSA_HASH_TYPE=md5
ROBOKASSA_FISCAL_MODE=false

BASE_URL=${BASE_URL}
SECRET_KEY=${SECRET_KEY}
COOKIE_SECURE=${COOKIE_SECURE}
COOKIE_DOMAIN=${DOMAIN}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
TRUSTED_HOSTS=${TRUSTED_HOSTS}

DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}

SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
EOF
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/python -c 'from app.config import settings; assert settings.database_url.startswith(\"postgresql\"); assert len(settings.secret_key)>=32; print(\"config ok\")'"
    ok ".env создан"
  else
    echo "  [dry] записал бы $APP_DIR/.env"
  fi
fi

if [ -f "$APP_DIR/.env" ] && [ "$DRY_RUN" != "1" ]; then
  sed -i "s|^XUI_URL=.*|XUI_URL=${XUI_URL}|" "$APP_DIR/.env"
  sed -i "s|^XUI_SUB_BASE_URL=.*|XUI_SUB_BASE_URL=${XUI_SUB_BASE_URL}|" "$APP_DIR/.env"
  grep -q '^XUI_URL=' "$APP_DIR/.env" || echo "XUI_URL=${XUI_URL}" >> "$APP_DIR/.env"
  grep -q '^XUI_SUB_BASE_URL=' "$APP_DIR/.env" || echo "XUI_SUB_BASE_URL=${XUI_SUB_BASE_URL}" >> "$APP_DIR/.env"
  ok "панель в .env: ${XUI_URL}"
fi

# ──────────── systemd ────────────
log "→ сервис $SERVICE"
if [ "$DRY_RUN" != "1" ]; then
  cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=LotAi subscription shop
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
    -b ${BIND_ADDR} \\
    --access-logfile - \\
    --error-logfile - \\
    --timeout 60 \\
    --graceful-timeout 30
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null
  systemctl restart "$SERVICE"
  sleep 3
  if systemctl is-active --quiet "$SERVICE"; then
    ok "$SERVICE запущен"
  else
    journalctl -u "$SERVICE" -n 40 --no-pager || true
    die "$SERVICE не запустился"
  fi
fi

# ──────────── Caddy ────────────
if [ "$USE_CADDY" = "1" ]; then
  log "→ Caddy"
  if [ "$DRY_RUN" != "1" ]; then
    cp -n /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak 2>/dev/null || true
    cat > /etc/caddy/Caddyfile <<EOF
${DOMAIN} {
    encode gzip
    reverse_proxy 127.0.0.1:${PORT}
}

${SUB_DOMAIN} {
    reverse_proxy 127.0.0.1:2096
}
EOF
    systemctl enable caddy >/dev/null 2>&1 || true
    systemctl reload caddy 2>/dev/null || systemctl restart caddy
    ok "Caddy настроен для ${DOMAIN}"
  fi
fi

# ──────────── Firewall ────────────
if [ "${SKIP_UFW:-0}" != "1" ]; then
  log "→ firewall"
  run "ufw --force default deny incoming"
  run "ufw --force default allow outgoing"
  run "ufw allow 22/tcp"
  if [ "$USE_CADDY" = "1" ]; then
    run "ufw allow 80/tcp"
    run "ufw allow 443/tcp"
  else
    run "ufw allow ${PORT}/tcp"
  fi
  run "ufw --force enable"
  ok "firewall включён, SSH открыт"
else
  warn "SKIP_UFW=1"
fi

# ──────────── Бэкап ────────────
if [ "$DRY_RUN" != "1" ] && [ "${SKIP_POSTGRES:-0}" != "1" ]; then
  mkdir -p "$APP_DIR/backups"
  cat > "$APP_DIR/scripts/backup_db.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR=${APP_DIR}/backups
mkdir -p "\$BACKUP_DIR"
STAMP=\$(date +%F_%H%M)
PGPASSWORD=\$(grep '^DATABASE_URL=' ${APP_DIR}/.env | sed -E 's|.*://[^:]+:([^@]+)@.*|\\1|')
pg_dump -h 127.0.0.1 -U ${DB_USER} ${DB_NAME} | gzip > "\$BACKUP_DIR/db_\${STAMP}.sql.gz"
find "\$BACKUP_DIR" -name 'db_*.sql.gz' -mtime +14 -delete
EOF
  chmod +x "$APP_DIR/scripts/backup_db.sh"
  chown -R "$APP_USER:$APP_USER" "$APP_DIR/scripts" "$APP_DIR/backups"
  CRON_LINE="0 3 * * * $APP_DIR/scripts/backup_db.sh"
  if ! sudo -u "$APP_USER" crontab -l 2>/dev/null | grep -qF "$APP_DIR/scripts/backup_db.sh"; then
    ( sudo -u "$APP_USER" crontab -l 2>/dev/null || true; echo "$CRON_LINE" ) | sudo -u "$APP_USER" crontab -
    ok "бэкап базы в 03:00"
  fi
fi

# ──────────── Панель ────────────
if [ "${SKIP_XUI_CHECK:-0}" != "1" ] && [ "$DRY_RUN" != "1" ]; then
  log "→ проверка панели"
  if curl -sf -m 5 -H "Authorization: Bearer ${XUI_API_TOKEN}" \
      "${XUI_URL}/panel/api/inbounds/get/${XUI_INBOUND_ID}" >/dev/null; then
    ok "панель доступна, inbound ${XUI_INBOUND_ID} найден"
  else
    warn "панель не ответила. Проверьте XUI_URL, токен и XUI_INBOUND_ID"
    warn "сайт поднимется, но оплата не создаст доступ, пока панель недоступна"
  fi
fi

if [ "$DRY_RUN" != "1" ]; then
  sleep 1
  if curl -sf -m 5 "http://127.0.0.1:${PORT}/healthz" >/dev/null; then
    ok "приложение отвечает"
  else
    journalctl -u "$SERVICE" -n 40 --no-pager || true
    die "нет ответа на /healthz"
  fi
fi

echo
echo "════════════════════════════════════════════════════════════"
ok "Установка завершена"
echo "════════════════════════════════════════════════════════════"
echo
echo "  Сайт:            ${BASE_URL}"
echo "  Кабинет:         ${BASE_URL}/account"
echo "  Админка:         ${BASE_URL}/admin"
echo "  Пароль админки:  ${ADMIN_PASSWORD:-см. $APP_DIR/.env}"
echo
echo "  Логи:            journalctl -u ${SERVICE} -f"
echo "  Перезапуск:      systemctl restart ${SERVICE}"
echo "  Конфиг:          ${APP_DIR}/.env"
echo
if [ -n "$DOMAIN" ]; then
  echo "  DNS: A-запись ${DOMAIN} должна указывать на этот сервер."
  echo
fi
