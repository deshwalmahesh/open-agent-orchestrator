"""Agent CRUD. Body = full AgentConfig (Pydantic validates at boundary, JSON in DB)."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.db.models import UserDB
from app.db.repos import create_agent, delete_agent, get_agent, list_agents, update_agent
from app.domain import AgentConfig
from app.users import current_active_user

router = APIRouter(prefix="/agents", tags=["agents"])


def _to_response(row) -> dict:
    """DB row → API shape. We echo the stored AgentConfig + DB metadata."""
    return {
        "id": str(row.id),
        "name": row.name,
        "config": row.config,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    config: AgentConfig,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await create_agent(
        session, user_id=user.id, name=config.name, config=config.model_dump(mode="json")
    )
    return _to_response(row)


@router.get("")
async def list_mine(
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    return [_to_response(r) for r in await list_agents(session, user_id=user.id)]


@router.get("/{agent_id}")
async def get_one(
    agent_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await get_agent(session, agent_id=agent_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    return _to_response(row)


@router.put("/{agent_id}")
async def update(
    agent_id: UUID,
    config: AgentConfig,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await update_agent(
        session,
        agent_id=agent_id,
        user_id=user.id,
        name=config.name,
        config=config.model_dump(mode="json"),
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    return _to_response(row)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    agent_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    if not await delete_agent(session, agent_id=agent_id, user_id=user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
