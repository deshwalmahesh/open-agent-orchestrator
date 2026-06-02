"""DB helpers — plain async functions. Caller owns the session/transaction.

All getter/list helpers filter by user_id so cross-user reads silently return None/[].
This collapses 403-vs-404 to 404 at the API layer — we don't leak existence."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentDB, ChatDB, MessageDB, PersonaDB, RunDB, RunEventDB, WorkflowDB
from app.domain import utcnow


async def create_agent(session: AsyncSession, *, user_id: UUID, name: str, config: dict) -> AgentDB:
    row = AgentDB(user_id=user_id, name=name, config=config)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_agent(session: AsyncSession, *, agent_id: UUID, user_id: UUID) -> AgentDB | None:
    """Returns None if not found OR not owned by user_id (404-vs-403 collapsed for UX simplicity)."""
    stmt = select(AgentDB).where(AgentDB.id == agent_id, AgentDB.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_agents(session: AsyncSession, *, user_id: UUID) -> list[AgentDB]:
    stmt = select(AgentDB).where(AgentDB.user_id == user_id).order_by(AgentDB.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def update_agent(
    session: AsyncSession, *, agent_id: UUID, user_id: UUID, name: str, config: dict
) -> AgentDB | None:
    row = await get_agent(session, agent_id=agent_id, user_id=user_id)
    if row is None:
        return None
    row.name = name
    row.config = config
    await session.commit()
    await session.refresh(row)
    return row


async def delete_agent(session: AsyncSession, *, agent_id: UUID, user_id: UUID) -> bool:
    row = await get_agent(session, agent_id=agent_id, user_id=user_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


# ---- Personas -------------------------------------------------------------

async def create_persona(session: AsyncSession, *, user_id: UUID, name: str, system_prompt: str) -> PersonaDB:
    row = PersonaDB(user_id=user_id, name=name, system_prompt=system_prompt)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_persona(session: AsyncSession, *, persona_id: UUID, user_id: UUID) -> PersonaDB | None:
    """Mine OR global. Used for read paths (and chat persona_id resolution)."""
    stmt = select(PersonaDB).where(
        PersonaDB.id == persona_id,
        or_(PersonaDB.user_id == user_id, PersonaDB.user_id.is_(None)),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_personas(session: AsyncSession, *, user_id: UUID) -> list[PersonaDB]:
    """User's own + globals. Globals first (visual grouping), then user's by name."""
    stmt = (
        select(PersonaDB)
        .where(or_(PersonaDB.user_id == user_id, PersonaDB.user_id.is_(None)))
        .order_by(PersonaDB.user_id.is_(None).desc(), PersonaDB.name)
    )
    return list((await session.execute(stmt)).scalars().all())


async def update_persona(
    session: AsyncSession, *, persona_id: UUID, user_id: UUID, name: str, system_prompt: str
) -> PersonaDB | None:
    """Only the OWNER can edit. Globals (user_id IS NULL) are read-only — returns None."""
    stmt = select(PersonaDB).where(PersonaDB.id == persona_id, PersonaDB.user_id == user_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    row.name = name
    row.system_prompt = system_prompt
    await session.commit()
    await session.refresh(row)
    return row


async def delete_persona(session: AsyncSession, *, persona_id: UUID, user_id: UUID) -> bool:
    """Only the OWNER can delete. Globals are protected — returns False."""
    stmt = select(PersonaDB).where(PersonaDB.id == persona_id, PersonaDB.user_id == user_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


# ---- Workflows ------------------------------------------------------------

async def create_workflow(
    session: AsyncSession, *, user_id: UUID, name: str, definition: dict, is_template: bool = False
) -> WorkflowDB:
    row = WorkflowDB(user_id=user_id, name=name, definition=definition, is_template=is_template)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_workflow(session: AsyncSession, *, workflow_id: UUID, user_id: UUID) -> WorkflowDB | None:
    """Returns the workflow if owned by `user_id` OR if it's a global template (user_id IS NULL)."""
    stmt = select(WorkflowDB).where(
        WorkflowDB.id == workflow_id,
        or_(WorkflowDB.user_id == user_id, WorkflowDB.user_id.is_(None)),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_workflows(session: AsyncSession, *, user_id: UUID) -> list[WorkflowDB]:
    """User's own workflows + global templates. Templates first (visual grouping), then mine."""
    stmt = (
        select(WorkflowDB)
        .where(or_(WorkflowDB.user_id == user_id, WorkflowDB.user_id.is_(None)))
        .order_by(WorkflowDB.is_template.desc(), WorkflowDB.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_templates(session: AsyncSession) -> list[WorkflowDB]:
    stmt = select(WorkflowDB).where(WorkflowDB.is_template.is_(True)).order_by(WorkflowDB.name)
    return list((await session.execute(stmt)).scalars().all())


async def update_workflow(
    session: AsyncSession, *, workflow_id: UUID, user_id: UUID, name: str, definition: dict
) -> WorkflowDB | None:
    """User can only edit their OWN workflows — templates are read-only."""
    stmt = select(WorkflowDB).where(WorkflowDB.id == workflow_id, WorkflowDB.user_id == user_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    row.name = name
    row.definition = definition
    await session.commit()
    await session.refresh(row)
    return row


async def delete_workflow(session: AsyncSession, *, workflow_id: UUID, user_id: UUID) -> bool:
    stmt = select(WorkflowDB).where(WorkflowDB.id == workflow_id, WorkflowDB.user_id == user_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


# ---- Chats ---------------------------------------------------------------

async def create_chat(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    persona_id: UUID | None = None,
    channel: str = "web",
    external_thread_id: str | None = None,
    title: str | None = None,
) -> ChatDB:
    row = ChatDB(
        user_id=user_id,
        agent_id=agent_id,
        persona_id=persona_id,
        channel=channel,
        external_thread_id=external_thread_id,
        title=title,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_chat(session: AsyncSession, *, chat_id: UUID, user_id: UUID) -> ChatDB | None:
    stmt = select(ChatDB).where(ChatDB.id == chat_id, ChatDB.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_chats(session: AsyncSession, *, user_id: UUID) -> list[ChatDB]:
    stmt = select(ChatDB).where(ChatDB.user_id == user_id).order_by(ChatDB.updated_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def delete_chat(session: AsyncSession, *, chat_id: UUID, user_id: UUID) -> bool:
    row = await get_chat(session, chat_id=chat_id, user_id=user_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


# ---- Runs ----------------------------------------------------------------

async def create_run(session: AsyncSession, *, chat_id: UUID, agent_id: UUID) -> RunDB:
    row = RunDB(chat_id=chat_id, agent_id=agent_id, status="running")
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_run(session: AsyncSession, *, run_id: UUID, user_id: UUID) -> RunDB | None:
    """Run is owned via chat → user. Single query with join keeps cross-user reads safe."""
    stmt = (
        select(RunDB)
        .join(ChatDB, ChatDB.id == RunDB.chat_id)
        .where(RunDB.id == run_id, ChatDB.user_id == user_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def finalize_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    status: str,
    total_tokens: dict | None = None,
    total_cost: float = 0.0,
    error: str | None = None,
) -> None:
    row = await session.get(RunDB, run_id)
    if row is None:
        return
    row.status = status
    row.ended_at = utcnow()
    if total_tokens is not None:
        row.total_tokens = total_tokens
    row.total_cost = total_cost
    row.error = error
    await session.commit()


# ---- Messages -----------------------------------------------------------

async def insert_message(
    session: AsyncSession,
    *,
    chat_id: UUID,
    run_id: UUID | None,
    sender: str,
    content: str,
    recipient: str | None = None,
) -> MessageDB:
    row = MessageDB(
        chat_id=chat_id, run_id=run_id, sender=sender, recipient=recipient, content=content
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_messages(
    session: AsyncSession, *, chat_id: UUID, limit: int | None = None
) -> list[MessageDB]:
    stmt = select(MessageDB).where(MessageDB.chat_id == chat_id).order_by(MessageDB.ts)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


# ---- Run events ---------------------------------------------------------

async def insert_event(
    session: AsyncSession, *, run_id: UUID, seq: int, event_type: str, data: dict
) -> None:
    session.add(RunEventDB(run_id=run_id, seq=seq, type=event_type, data=data))
    await session.commit()


async def list_events(
    session: AsyncSession, *, run_id: UUID, after_seq: int = 0
) -> list[RunEventDB]:
    stmt = (
        select(RunEventDB)
        .where(RunEventDB.run_id == run_id, RunEventDB.seq > after_seq)
        .order_by(RunEventDB.seq)
    )
    return list((await session.execute(stmt)).scalars().all())
