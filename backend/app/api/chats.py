"""Chat CRUD + PATCH (agent reassignment). Validates cross-references:
agent_id and persona_id (if set) must belong to the requesting user."""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.db import get_async_session
from app.db.models import AgentDB, UserDB
from app.db.repos import (
    create_chat,
    delete_chat,
    get_agent,
    get_chat,
    get_persona,
    list_chats,
    list_messages,
    update_chat,
)
from app.services.run_service import start_run
from app.users import current_active_user

log = structlog.get_logger()

router = APIRouter(prefix="/chats", tags=["chats"])


class ChatCreateBody(BaseModel):
    agent_id: UUID
    persona_id: UUID | None = None
    channel: Literal["web", "slack"] = "web"
    external_thread_id: str | None = None
    title: str | None = None


class FileAttachment(BaseModel):
    name: str
    content_base64: str
    mime_type: str


class MessageBody(BaseModel):
    text: str
    files: list[FileAttachment] = Field(default_factory=list)


class ChatPatchBody(BaseModel):
    agent_id: UUID | None = None
    persona_id: UUID | None = None


def _to_response(row, agent_name: str | None = None) -> dict:
    return {
        "id": str(row.id),
        "agent_id": str(row.agent_id) if row.agent_id else None,
        "agent_name": agent_name,
        "persona_id": str(row.persona_id) if row.persona_id else None,
        "channel": row.channel,
        "external_thread_id": row.external_thread_id,
        "title": row.title,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: ChatCreateBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    # Cross-ref ownership checks: prevent users from attaching others' resources.
    if await get_agent(session, agent_id=body.agent_id, user_id=user.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    if body.persona_id is not None:
        if await get_persona(session, persona_id=body.persona_id, user_id=user.id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found")

    row = await create_chat(
        session,
        user_id=user.id,
        agent_id=body.agent_id,
        persona_id=body.persona_id,
        channel=body.channel,
        external_thread_id=body.external_thread_id,
        title=body.title,
    )
    return _to_response(row)


@router.get("")
async def list_mine(
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    chats = await list_chats(session, user_id=user.id)
    agent_ids = list({c.agent_id for c in chats if c.agent_id})
    names: dict[UUID, str] = {}
    if agent_ids:
        rows = (await session.execute(
            select(AgentDB.id, AgentDB.name).where(AgentDB.id.in_(agent_ids))
        )).all()
        names = {row.id: row.name for row in rows}
    return [_to_response(c, agent_name=names.get(c.agent_id)) for c in chats]


@router.get("/{chat_id}")
async def get_one(
    chat_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await get_chat(session, chat_id=chat_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")
    agent_name = None
    if row.agent_id:
        agent_row = await session.get(AgentDB, row.agent_id)
        agent_name = agent_row.name if agent_row else None
    return _to_response(row, agent_name=agent_name)


@router.patch("/{chat_id}")
async def patch(
    chat_id: UUID,
    body: ChatPatchBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Reassign agent or persona on an existing chat."""
    if body.agent_id is not None:
        if await get_agent(session, agent_id=body.agent_id, user_id=user.id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    if body.persona_id is not None:
        if await get_persona(session, persona_id=body.persona_id, user_id=user.id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found")
    row = await update_chat(
        session,
        chat_id=chat_id,
        user_id=user.id,
        agent_id=body.agent_id,
        persona_id=body.persona_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")
    return _to_response(row)


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    chat_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    if not await delete_chat(session, chat_id=chat_id, user_id=user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")


@router.post("/{chat_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def post_message(
    chat_id: UUID,
    body: MessageBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Schedule a run. Returns run_id immediately; observe via SSE on /runs/{id}/events."""
    chat = await get_chat(session, chat_id=chat_id, user_id=user.id)
    if chat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")
    if chat.agent_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "no agent assigned — use PATCH /chats/{id} to assign one first",
        )
    files_raw = [f.model_dump() for f in body.files]
    run_id = await start_run(session, chat_id=chat_id, user_text=body.text, files=files_raw)
    log.info("chat.message", chat_id=str(chat_id), run_id=str(run_id), user_id=str(user.id))
    return {"run_id": str(run_id), "chat_id": str(chat_id)}


@router.get("/{chat_id}/messages")
async def get_messages(
    chat_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    if await get_chat(session, chat_id=chat_id, user_id=user.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat not found")
    rows = await list_messages(session, chat_id=chat_id)
    return [
        {
            "id": str(r.id),
            "run_id": str(r.run_id) if r.run_id else None,
            "sender": r.sender,
            "recipient": r.recipient,
            "content": r.content,
            "ts": r.ts.isoformat(),
        }
        for r in rows
    ]
