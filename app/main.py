import ipaddress
import logging
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import settings
from .database import Base, SessionLocal, engine, ensure_schema
from .models import Plan
from .routers import account, payment, shop
from .sync import sync_all
from .xui_client import xui

# ════════════════ Логирование ════════════════
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ════════════════ Sentry (опционально) ════════════════
_sentry_dsn = getattr(settings, "sentry_dsn", "") or ""
if _sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=_sentry_dsn, traces_sample_rate=0.1)
        logger.info("Sentry initialized")
    except Exception as e:
        logger.warning("Sentry init failed: %s", e)


# ════════════════ Приложение ════════════════
app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)


# ════════════════ Trusted hosts ════════════════
# Домен из TRUSTED_HOSTS обязателен. IP и *.nip.io пускаем всегда:
# сайт на VPS открывают по адресу машины, а не по имени из .env.
def host_allowed(host: str, allowed: list[str]) -> bool:
    host = (host or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1", "testserver"}:
        return True
    for pattern in allowed:
        if host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:])):
            return True
    if host.endswith(".nip.io"):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class ShopHostMiddleware:
    def __init__(self, app: ASGIApp, allowed_hosts: list[str]) -> None:
        self.app = app
        self.allow_any = "*" in allowed_hosts
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.allow_any or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        host = Headers(scope=scope).get("host", "").split(":")[0]
        if host_allowed(host, self.allowed_hosts):
            await self.app(scope, receive, send)
            return
        await PlainTextResponse("Invalid host header", status_code=400)(scope, receive, send)


_hosts_env = getattr(settings, "trusted_hosts", "") or ""
_allowed_hosts = (
    [h.strip().lower() for h in _hosts_env.split(",") if h.strip()]
    if _hosts_env
    else ["*"]
)
app.add_middleware(ShopHostMiddleware, allowed_hosts=_allowed_hosts)


# ════════════════ Статика ════════════════
app.mount("/static", StaticFiles(directory="static"), name="static")


# ════════════════ Роутеры ════════════════
app.include_router(shop.router)
app.include_router(payment.router, prefix="/payment")
app.include_router(account.router)

# Админка тарифов — подключаем, только если задан пароль
if getattr(settings, "admin_password", ""):
    try:
        from .routers import admin
        app.include_router(admin.router)
        logger.info("Admin router enabled at /admin")
    except Exception as e:
        logger.warning("Admin router not available: %s", e)


# ════════════════ Security headers ════════════════
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # HSTS отправляем только если сайт на HTTPS
    if settings.base_url.startswith("https://"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ════════════════ Health check ════════════════
@app.get("/healthz")
def healthz():
    """Liveness/readiness для UptimeRobot, healthchecks.io, Docker healthcheck."""
    return JSONResponse({"status": "ok"})


# ════════════════ Startup ════════════════
def _sync_loop() -> None:
    while True:
        time.sleep(max(settings.xui_sync_interval, 15))
        db = SessionLocal()
        try:
            count = sync_all(db)
            logger.info("3x-ui sync: обновлено подписок %s", count)
        except Exception:
            logger.exception("3x-ui sync failed")
        finally:
            db.close()


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    logger.info("Database schema ready")
    if settings.xui_sync_interval > 0 and not settings.xui_mock_mode:
        threading.Thread(target=_sync_loop, name="xui-sync", daemon=True).start()
        logger.info("3x-ui sync every %ss", settings.xui_sync_interval)

    # Проверка 3x-ui: не роняем приложение, только предупреждаем
    if not settings.xui_mock_mode:
        try:
            xui._ensure_login()
            ib = xui.get_inbound(settings.xui_inbound_id)
            logger.info(
                "3x-ui OK: inbound %s (%s), port %s",
                ib.get("remark"), ib.get("protocol"), ib.get("port"),
            )
        except Exception as e:
            logger.error("3x-ui недоступен: %s", e)

    # Предупреждения о конфигурации для прода
    if not settings.debug:
        if not settings.secret_key:
            logger.warning("SECRET_KEY пуст — небезопасно для прода")
        if len(settings.secret_key) < 32:
            logger.warning("SECRET_KEY короче 32 символов — небезопасно для прода")
        if not settings.cookie_secure:
            logger.warning("COOKIE_SECURE=false — cookie будет доступна по HTTP")
        if not settings.robokassa_mock and not settings.robokassa_password2:
            logger.warning("ROBOKASSA_MOCK=false, но ROBOKASSA_PASSWORD2 пуст — Result URL не пройдёт проверку")
        if settings.xui_mock_mode:
            logger.warning("XUI_MOCK_MODE=true — клиенты не создаются в реальной панели")

    # Seed тарифов при первом запуске
    db = SessionLocal()
    try:
        if not db.query(Plan).first():
            for name, days, gb, price in [
                ("Старт", 30, 50, 199),
                ("Стандарт", 30, 200, 349),
                ("Премиум (безлимит)", 30, 0, 599),
                ("Годовой", 365, 0, 4999),
            ]:
                db.add(Plan(name=name, days=days, traffic_gb=gb, price=price))
            db.commit()
            logger.info("Seeded %d plans", 4)
        else:
            logger.info("Plans already exist, skipping seed")
    finally:
        db.close()


@app.on_event("shutdown")
def shutdown():
    logger.info("Application shutting down")