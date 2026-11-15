from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

_sqlite = settings.database_url.startswith("sqlite")
_engine_kwargs = {"pool_pre_ping": True}
if _sqlite:
    _engine_kwargs = {
        "connect_args": {"check_same_thread": False},
        "pool_pre_ping": True,
    }
    if ":memory:" in settings.database_url:
        from sqlalchemy.pool import StaticPool
        _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _table_names(conn) -> set[str]:
    if engine.dialect.name == "sqlite":
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table'")
        ).fetchall()
        return {row[0] for row in rows}
    rows = conn.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    ).fetchall()
    return {row[0] for row in rows}


def _column_names(conn, table: str) -> set[str]:
    if engine.dialect.name == "sqlite":
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return {row[1] for row in rows}
    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table"
        ),
        {"table": table},
    ).fetchall()
    return {row[0] for row in rows}


def ensure_schema() -> None:
    """Добавляет колонки в уже созданную базу. create_all новые поля не дописывает."""
    # Пул мог запомнить старую схему SQLite. Для миграции берём новое соединение.
    engine.dispose()
    bool_default = "BOOLEAN DEFAULT TRUE" if engine.dialect.name == "postgresql" else "BOOLEAN DEFAULT 1"
    timestamp = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
    wanted = {
        "subscriptions": {
            "name": "VARCHAR DEFAULT ''",
            "comment": "VARCHAR DEFAULT ''",
            "client_uuid": "VARCHAR DEFAULT ''",
            "total_bytes": "BIGINT DEFAULT 0",
            "upload_bytes": "BIGINT DEFAULT 0",
            "download_bytes": "BIGINT DEFAULT 0",
            "panel_enable": bool_default,
            "last_sync_at": timestamp,
            "sync_error": "VARCHAR DEFAULT ''",
        },
        "orders": {
            "subscription_id": "INTEGER",
            "kind": "VARCHAR DEFAULT 'new'",
        },
    }
    with engine.begin() as conn:
        tables = _table_names(conn)
        for table, columns in wanted.items():
            if table not in tables:
                continue
            existing = _column_names(conn, table)
            for name, ddl in columns.items():
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))