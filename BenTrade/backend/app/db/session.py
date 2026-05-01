"""Async session factory for the BenTrade decision history database.

Single source of truth for building an ``async_sessionmaker`` from a
configured ``AsyncEngine``. Stored on ``app.state.history_session_maker``
by the FastAPI startup sequence and consumed by the history_recorder
service (Step 3) and any ad-hoc read paths.

Usage
-----
    session_maker = build_session_maker(engine)
    async with session_maker() as session:
        session.add(decision)
        await session.commit()
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)


def build_session_maker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Construct an async_sessionmaker with writer-friendly defaults.

    ``expire_on_commit=False`` keeps ORM objects usable after ``commit()``
    (important for fire-and-forget tasks that log the committed id).
    ``autoflush=False`` makes behaviour predictable for tasks that build a
    small object graph and commit once.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


__all__ = ["build_session_maker"]
