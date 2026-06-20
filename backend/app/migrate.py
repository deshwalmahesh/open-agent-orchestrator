"""One-shot schema bootstrap, run as a SINGLE pod (Helm pre-upgrade Job / init step).

Why a dedicated entrypoint: when N API + M worker replicas all boot at once, each
running create_all()/seed_defaults() in its lifespan races on the same ALTER/seed.
Running this once before the rollout means every pod's lifespan create_all finds the
schema already present and no-ops — no concurrent DDL.

ponytail: reuses the existing idempotent create_all + seed (the inline ALTER block).
Versioned/reversible Alembic migrations are the next step when a schema change needs
rollback; until then this single-runner kills the race, which was the real bug.
"""
import asyncio

import structlog

from app.config import get_settings
from app.db import create_all, seed_defaults
from app.logging import configure_logging

log = structlog.get_logger()


async def main() -> None:
    configure_logging(get_settings().log_level)
    log.info("migrate.start")
    await create_all()
    await seed_defaults()
    log.info("migrate.done")


if __name__ == "__main__":
    asyncio.run(main())
