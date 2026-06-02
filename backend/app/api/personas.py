"""Persona CRUD. Persona = named system_prompt belonging to a user."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.db.models import UserDB
from app.db.repos import create_persona, delete_persona, get_persona, list_personas, update_persona
from app.users import current_active_user

router = APIRouter(prefix="/personas", tags=["personas"])


class PersonaBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    system_prompt: str = Field(min_length=1)


def _to_response(row) -> dict:
    # owner_id=None marks a global persona — UI shows it but disables edit/delete.
    return {
        "id": str(row.id),
        "name": row.name,
        "system_prompt": row.system_prompt,
        "owner_id": str(row.user_id) if row.user_id else None,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: PersonaBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await create_persona(session, user_id=user.id, name=body.name, system_prompt=body.system_prompt)
    return _to_response(row)


@router.get("")
async def list_mine(
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    return [_to_response(r) for r in await list_personas(session, user_id=user.id)]


@router.get("/{persona_id}")
async def get_one(
    persona_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await get_persona(session, persona_id=persona_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found")
    return _to_response(row)


@router.put("/{persona_id}")
async def update(
    persona_id: UUID,
    body: PersonaBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await update_persona(
        session, persona_id=persona_id, user_id=user.id, name=body.name, system_prompt=body.system_prompt
    )
    if row is None:
        # 404 covers both "doesn't exist" and "exists but global / not mine".
        raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found or not editable")
    return _to_response(row)


@router.delete("/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    persona_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    if not await delete_persona(session, persona_id=persona_id, user_id=user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found or not deletable")
