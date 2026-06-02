"""Idempotent startup seed: 2 workflow templates (TASK.md requirement).

Templates are user_id=NULL, is_template=True — visible to every user, read-only.
Keyed by stable `name`; INSERT-IF-NOT-EXISTS so restarts don't duplicate.

Agent `ref` fields hold ROLE NAMES (researcher, writer, worker), not agent UUIDs —
the run-time binds these to the caller's agents. UI surfaces the mapping prompt.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.db.models import WorkflowDB
from app.domain import WorkflowDef, NodeDef, EdgeDef

log = structlog.get_logger()


_TEMPLATES: list[WorkflowDef] = [
    WorkflowDef(
        name="research-and-write",
        description="Researcher gathers facts; Writer turns them into a structured piece.",
        entry="researcher",
        is_template=True,
        nodes=[
            NodeDef(id="researcher", type="agent", ref="researcher"),
            NodeDef(id="writer", type="agent", ref="writer"),
            NodeDef(id="end", type="end"),
        ],
        edges=[
            EdgeDef(id="r_to_w", source="researcher", target="writer"),
            EdgeDef(id="w_to_end", source="writer", target="end"),
        ],
    ),
    WorkflowDef(
        name="supervised-loop",
        description="Worker produces output; reviewer condition routes to revision or done.",
        entry="worker",
        is_template=True,
        nodes=[
            NodeDef(id="worker", type="agent", ref="worker"),
            NodeDef(id="review", type="condition"),
            NodeDef(id="end", type="end"),
        ],
        edges=[
            EdgeDef(id="w_to_r", source="worker", target="review"),
            EdgeDef(
                id="r_revise",
                source="review",
                target="worker",
                condition="'NEEDS_REVISION' in last_message_content",
            ),
            EdgeDef(id="r_done", source="review", target="end"),
        ],
    ),
]


async def seed_templates(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Insert each template if its name doesn't already exist as a template."""
    async with session_factory() as session:
        for wf in _TEMPLATES:
            stmt = select(WorkflowDB.id).where(
                WorkflowDB.name == wf.name, WorkflowDB.is_template.is_(True)
            )
            if (await session.execute(stmt)).scalar_one_or_none() is not None:
                continue
            session.add(
                WorkflowDB(
                    user_id=None,
                    name=wf.name,
                    is_template=True,
                    definition=wf.model_dump(mode="json"),
                )
            )
            log.info("seed.template.inserted", name=wf.name)
        await session.commit()
