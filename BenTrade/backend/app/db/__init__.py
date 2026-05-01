"""BenTrade decision history database package.

SQLite-on-NAS persistence layer for immutable decision records.
Mirrors the Company Evaluator pattern: async SQLAlchemy + aiosqlite,
WAL mode, raw DDL via ``Base.metadata.create_all`` (no Alembic).

See ``backend/app/db/models.py`` for the 9-table schema.
"""

from app.db.engine import create_history_engine, init_db, verify_pragmas
from app.db.session import build_session_maker

__all__ = [
    "create_history_engine",
    "init_db",
    "verify_pragmas",
    "build_session_maker",
]
