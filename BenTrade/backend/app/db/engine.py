"""Async SQLAlchemy engine for the BenTrade decision history database.

Responsibilities
----------------
* Build a single ``AsyncEngine`` bound to the configured SQLite URL.
* Enable WAL journaling and enforce foreign keys on every raw connection
  via a ``connect`` event listener.
* Provide ``init_db()`` which calls ``Base.metadata.create_all`` to
  materialise the schema on startup (mirrors Company Evaluator's raw-DDL
  pattern — no Alembic).

Usage
-----
    engine = create_history_engine(settings.HISTORY_DB_URL)
    await init_db(engine)
    ...
    await engine.dispose()
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.models import Base

_LOG = logging.getLogger(__name__)


def _ensure_parent_dir(url: str) -> None:
    """Make sure the directory for a SQLite file URL exists.

    Uses SQLAlchemy's URL parser so UNC paths survive intact. The
    ``database`` component that comes back from ``make_url`` is the actual
    filesystem path aiosqlite will hand to ``sqlite3.connect``:

    * ``sqlite:///C:/tmp/x.db``             → ``C:/tmp/x.db``
    * ``sqlite://///host/share/x.db``       → ``//host/share/x.db``  (UNC)
    * ``sqlite:///\\host\\share\\x.db``     → ``\\host\\share\\x.db`` (UNC)
    """
    from sqlalchemy.engine.url import make_url

    try:
        db_path = make_url(url).database
    except Exception:
        return
    if not db_path:
        return
    try:
        parent = Path(db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        # Do not abort engine creation on permission/path weirdness — the
        # subsequent connect attempt will surface a clear error.
        _LOG.warning(
            "event=history_db_parent_dir_check error=%s path=%s", exc, db_path
        )


def create_history_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Build the async engine and register WAL + foreign-keys pragma listener.

    Parameters
    ----------
    url:
        SQLAlchemy URL. Must be a SQLite URL. The aiosqlite driver is
        injected automatically if the caller passed plain ``sqlite://``.
    echo:
        Forwarded to ``create_async_engine`` for SQL logging.
    """
    if url.startswith("sqlite://") and not url.startswith("sqlite+aiosqlite://"):
        url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    _ensure_parent_dir(url)

    engine = create_async_engine(url, echo=echo, future=True)

    # Register pragma listener on the underlying sync engine so every new
    # raw connection (including those opened by aiosqlite's thread pool)
    # gets WAL + FK enforcement.
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA synchronous=NORMAL;")  # WAL-safe, faster than FULL
        finally:
            cursor.close()

    _LOG.info("event=history_engine_created url=%s", _redact(url))
    return engine


async def init_db(engine: AsyncEngine) -> None:
    """Materialise the schema (idempotent — ``create_all`` skips existing tables)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _LOG.info("event=history_db_schema_ready tables=%d", len(Base.metadata.tables))


async def verify_pragmas(engine: AsyncEngine) -> dict[str, str]:
    """Read back journal_mode and foreign_keys for startup verification.

    Returned dict looks like ``{"journal_mode": "wal", "foreign_keys": "1"}``.
    Separate helper so the startup sequence can log what it actually got.
    """
    from sqlalchemy import text

    result: dict[str, str] = {}
    async with engine.connect() as conn:
        jm = (await conn.execute(text("PRAGMA journal_mode;"))).scalar()
        fk = (await conn.execute(text("PRAGMA foreign_keys;"))).scalar()
        result["journal_mode"] = str(jm).lower() if jm is not None else "unknown"
        result["foreign_keys"] = str(fk) if fk is not None else "unknown"
    return result


def _redact(url: str) -> str:
    """Paths are fine to log; redact any password component if present."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            creds, tail = rest.rsplit("@", 1)
            return f"{scheme}://***@{tail}"
    return url


__all__ = ["create_history_engine", "init_db", "verify_pragmas"]
