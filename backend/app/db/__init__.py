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
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from app.db import models  # noqa: F401 — register tables with Base.metadata
    log.info("db.create_all", url=get_settings().database_url)
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Inline column migration: ADD COLUMN raises if the column already exists.
        # SQLite has no IF NOT EXISTS for ADD COLUMN, and on Postgres a failed DDL
        # aborts the whole transaction (next statement gets "transaction is aborted"
        # until ROLLBACK). Wrap each ALTER in a SAVEPOINT so a duplicate-column error
        # only rolls back that savepoint, leaving the parent transaction valid.
        for col in (
            "plan VARCHAR(20) NOT NULL DEFAULT 'free'",
            "slack_bot_token VARCHAR(200)",
            "slack_app_token VARCHAR(200)",
        ):
            try:
                async with conn.begin_nested():
                    await conn.execute(text(f'ALTER TABLE "user" ADD COLUMN {col}'))
            except (OperationalError, ProgrammingError):
                pass


_DEFAULT_SUPERVISOR_PROMPT = (
    "You are a supervisor agent. Coordinate sub-agents and tools to solve the user's task. "
    "Delegate to a sub-agent when its role matches; call a tool when needed. "
    "Be concise, accurate, and decisive."
)


async def seed_defaults() -> None:
    """Seed read-only global rows that every workspace expects (user_id IS NULL).
    Idempotent — safe to call on every startup. Currently: one Default Supervisor persona."""
    from sqlalchemy import select

    from app.db.models import PersonaDB

    async with get_session_factory()() as session:
        existing = (await session.execute(
            select(PersonaDB).where(
                PersonaDB.user_id.is_(None),
                PersonaDB.name == "Default Supervisor",
            )
        )).scalar_one_or_none()
        if existing is not None:
            return
        session.add(PersonaDB(user_id=None, name="Default Supervisor", system_prompt=_DEFAULT_SUPERVISOR_PROMPT))
        await session.commit()
        log.info("db.seed.default_persona_created")
