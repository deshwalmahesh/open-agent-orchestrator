"""Skill CRUD. Skill = named knowledge/instruction document injected into agent prompt."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.db.models import UserDB
from app.db.repos import create_skill, delete_skill, get_skill, list_skills, update_skill
from app.users import current_active_user

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1)


def _to_response(row) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "content": row.content,
        "owner_id": str(row.user_id) if row.user_id else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: SkillBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await create_skill(session, user_id=user.id, name=body.name, content=body.content)
    return _to_response(row)


@router.get("")
async def list_mine(
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    return [_to_response(r) for r in await list_skills(session, user_id=user.id)]


@router.get("/{skill_id}")
async def get_one(
    skill_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await get_skill(session, skill_id=skill_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found")
    return _to_response(row)


@router.put("/{skill_id}")
async def update(
    skill_id: UUID,
    body: SkillBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await update_skill(
        session, skill_id=skill_id, user_id=user.id, name=body.name, content=body.content
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found or not editable")
    return _to_response(row)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    skill_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    if not await delete_skill(session, skill_id=skill_id, user_id=user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "skill not found or not deletable")
