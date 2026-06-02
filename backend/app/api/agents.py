"""Agent CRUD. Body = full AgentConfig (Pydantic validates at boundary, JSON in DB).

Sub-agent validation (cycles, depth, cross-user, name collisions) runs on
POST and PUT before any DB write.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.db.models import AgentDB, UserDB
from app.db.repos import create_agent, delete_agent, get_agent, list_agents, update_agent
from app.domain import AgentConfig
from app.runtime.agent import MAX_AGENT_DEPTH
from app.runtime.tools import REGISTRY
from app.users import current_active_user

log = structlog.get_logger()

router = APIRouter(prefix="/agents", tags=["agents"])


async def _validate_subagent_tree(
    cfg: AgentConfig,
    user_id: UUID,
    session: AsyncSession,
    *,
    self_id: UUID | None = None,
) -> None:
    """Validate the sub-agent tree rooted at `cfg`. Raises HTTPException(400) on:
    - sub-agent not found / not owned by user
    - cycle in the delegation graph
    - depth exceeds MAX_AGENT_DEPTH
    - sub-agent name collides with a REGISTRY tool name
    """
    if not cfg.subagents:
        return

    registry_names = set(REGISTRY.keys())

    stmt = select(AgentDB).where(AgentDB.user_id == user_id)
    rows = (await session.execute(stmt)).scalars().all()
    by_id: dict[UUID, AgentConfig] = {}
    for r in rows:
        by_id[r.id] = AgentConfig.model_validate(r.config)

    if self_id is not None:
        by_id[self_id] = cfg

    def _check_refs(agent_cfg: AgentConfig, visited: set[UUID], depth: int) -> None:
        if depth > MAX_AGENT_DEPTH:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"sub-agent nesting depth exceeds {MAX_AGENT_DEPTH}",
            )
        for sa_id in agent_cfg.subagents:
            if sa_id not in by_id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"sub-agent {sa_id} not found or not owned by you",
                )
            sa_cfg = by_id[sa_id]
            if sa_cfg.name in registry_names:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"sub-agent name '{sa_cfg.name}' collides with a built-in tool",
                )
            if sa_id in visited:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"cycle detected: agent {sa_id} appears in its own delegation chain",
                )
            visited.add(sa_id)
            _check_refs(sa_cfg, visited, depth + 1)
            visited.discard(sa_id)

    start_visited: set[UUID] = set()
    if self_id is not None:
        start_visited.add(self_id)
    _check_refs(cfg, start_visited, depth=1)


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
    await _validate_subagent_tree(config, user.id, session)
    row = await create_agent(
        session, user_id=user.id, name=config.name, config=config.model_dump(mode="json")
    )
    log.info("agent.created", agent_id=str(row.id), name=row.name, user_id=str(user.id))
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
    await _validate_subagent_tree(config, user.id, session, self_id=agent_id)
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
    log.info("agent.deleted", agent_id=str(agent_id), user_id=str(user.id))
