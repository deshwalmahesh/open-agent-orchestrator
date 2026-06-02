"""Async SQLAlchemy engine + session factory + Base. SQLite v1; Postgres via URL swap."""
from __future__ import annotations

from collections.abc import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

log = structlog.get_logger()


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, future=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with get_session_factory()() as session:
        yield session


async def create_all() -> None:
    """Idempotent schema bootstrap — no Alembic in v1; prod swaps in migrations."""
    from app.db import models  # noqa: F401 — register tables with Base.metadata
    log.info("db.create_all", url=get_settings().database_url)
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
