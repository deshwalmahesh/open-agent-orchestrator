"""Async SQLAlchemy engine + session factory + Base. SQLite v1; Postgres via URL swap."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import structlog
import yaml
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
    from sqlalchemy import inspect, text

    from app.db import models  # noqa: F401 — register tables with Base.metadata
    log.info("db.create_all", url=get_settings().database_url)
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Inline column migration. Inspect the existing schema first so we
        # don't ALTER a column that's already there — that avoids the noisy
        # Postgres ERROR log on every startup.
        def _cols(sync_conn, table: str) -> set[str]:
            return {c["name"] for c in inspect(sync_conn).get_columns(table)}

        adds: dict[str, list[tuple[str, str]]] = {
            "user": [
                ("plan", "VARCHAR(20) NOT NULL DEFAULT 'free'"),
                ("slack_bot_token", "VARCHAR(200)"),
                ("slack_app_token", "VARCHAR(200)"),
            ],
            "agents": [("deployed_at", "TIMESTAMP WITH TIME ZONE")],
        }
        for table, cols in adds.items():
            existing = await conn.run_sync(lambda sc, t=table: _cols(sc, t))
            quoted = f'"{table}"' if table == "user" else table
            for name, defn in cols:
                if name not in existing:
                    await conn.execute(text(f"ALTER TABLE {quoted} ADD COLUMN {name} {defn}"))

        # Drop obsolete columns. Persona moved from per-chat to per-agent.
        chat_cols = await conn.run_sync(lambda sc: _cols(sc, "chats"))
        if "persona_id" in chat_cols:
            await conn.execute(text("ALTER TABLE chats DROP COLUMN persona_id"))


_PERSONAS_YAML = Path(__file__).parent / "seeds" / "personas.yaml"


async def seed_defaults() -> None:
    """Seed/refresh global personas (user_id IS NULL) from seeds/personas.yaml.

    Idempotent. For each YAML entry: insert if missing, or update the row's
    system_prompt if it has diverged from the YAML (the file is the source of
    truth — editing it is how bundled prompts evolve, no migration needed).
    Name is the natural key."""
    from sqlalchemy import select

    from app.db.models import PersonaDB

    entries = yaml.safe_load(_PERSONAS_YAML.read_text())
    if not entries:
        return

    yaml_names = {entry["name"] for entry in entries}
    async with get_session_factory()() as session:
        for entry in entries:
            name = entry["name"]
            prompt = entry["system_prompt"]
            row = (await session.execute(
                select(PersonaDB).where(
                    PersonaDB.user_id.is_(None), PersonaDB.name == name,
                )
            )).scalar_one_or_none()
            if row is None:
                session.add(PersonaDB(user_id=None, name=name, system_prompt=prompt))
                log.info("db.seed.persona_created", name=name)
            elif row.system_prompt != prompt:
                row.system_prompt = prompt
                log.info("db.seed.persona_updated", name=name)
        # Drop any global personas no longer present in the YAML — they're
        # leftovers from earlier seed code. YAML is the source of truth.
        orphans = (await session.execute(
            select(PersonaDB).where(
                PersonaDB.user_id.is_(None),
                PersonaDB.name.not_in(yaml_names),
            )
        )).scalars().all()
        for row in orphans:
            log.info("db.seed.persona_removed_orphan", name=row.name)
            await session.delete(row)
        await session.commit()
