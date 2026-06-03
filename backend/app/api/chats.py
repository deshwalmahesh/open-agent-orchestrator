"""Chat CRUD + PATCH (agent reassignment). Validates cross-references:
agent_id (if set) must belong to the requesting user."""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.db import get_async_session
from app.db.models import AgentDB, MessageDB, UserDB
from app.db.repos import (
    create_chat,
    delete_chat,
    get_agent,
    get_chat,
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


def _to_response(row, agent_name: str | None = None, preview: str | None = None) -> dict:
    return {
        "id": str(row.id),
        "agent_id": str(row.agent_id) if row.agent_id else None,
        "agent_name": agent_name,
        "channel": row.channel,
        "external_thread_id": row.external_thread_id,
        "title": row.title,
        "preview": preview,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: ChatCreateBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    # Cross-ref ownership check: prevent attaching another user's agent.
    agent_row = await get_agent(session, agent_id=body.agent_id, user_id=user.id)
    if agent_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    if agent_row.deployed_at is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "pipeline is in Draft — deploy it before starting a chat")

    row = await create_chat(
        session,
        user_id=user.id,
        agent_id=body.agent_id,
        channel=body.channel,
        external_thread_id=body.external_thread_id,
        title=body.title,
    )
    return _to_response(row)


_PREVIEW_LEN = 80


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

    # Preview = first user message per chat (single query, no N+1).
    previews: dict[UUID, str] = {}
    chat_ids = [c.id for c in chats]
    if chat_ids:
        earliest = (
            select(MessageDB.chat_id, func.min(MessageDB.ts).label("ts"))
            .where(MessageDB.chat_id.in_(chat_ids), MessageDB.sender == "user")
            .group_by(MessageDB.chat_id)
            .subquery()
        )
        msg_rows = (await session.execute(
            select(MessageDB.chat_id, MessageDB.content).join(
                earliest,
                (MessageDB.chat_id == earliest.c.chat_id) & (MessageDB.ts == earliest.c.ts),
            )
        )).all()
        for r in msg_rows:
            content = (r.content or "").replace("\n", " ").strip()
            previews[r.chat_id] = (
                content[:_PREVIEW_LEN] + "…" if len(content) > _PREVIEW_LEN else content
            )

    return [
        _to_response(c, agent_name=names.get(c.agent_id), preview=previews.get(c.id))
        for c in chats
    ]


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
    """Reassign the chat's agent (= pipeline)."""
    if body.agent_id is not None:
        agent_row = await get_agent(session, agent_id=body.agent_id, user_id=user.id)
        if agent_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
        if agent_row.deployed_at is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "pipeline is in Draft — deploy it before reassigning")
    row = await update_chat(
        session, chat_id=chat_id, user_id=user.id, agent_id=body.agent_id,
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
