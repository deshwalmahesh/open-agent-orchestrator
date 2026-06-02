"""Workflow CRUD. Templates (is_template=True, user_id IS NULL) are read-only and
visible to all users via GET /workflows and GET /workflows/templates."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.db.models import UserDB
from app.db.repos import (
    create_workflow,
    delete_workflow,
    get_workflow,
    list_templates,
    list_workflows,
    update_workflow,
)
from app.domain import WorkflowDef
from app.users import current_active_user

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _to_response(row) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "is_template": row.is_template,
        "owner_id": str(row.user_id) if row.user_id else None,
        "definition": row.definition,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    definition: WorkflowDef,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await create_workflow(
        session,
        user_id=user.id,
        name=definition.name,
        definition=definition.model_dump(mode="json"),
        is_template=False,
    )
    return _to_response(row)


@router.get("")
async def list_mine_and_templates(
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    return [_to_response(r) for r in await list_workflows(session, user_id=user.id)]


@router.get("/templates")
async def list_templates_endpoint(
    _: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    return [_to_response(r) for r in await list_templates(session)]


@router.get("/{workflow_id}")
async def get_one(
    workflow_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await get_workflow(session, workflow_id=workflow_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow not found")
    return _to_response(row)


@router.put("/{workflow_id}")
async def update(
    workflow_id: UUID,
    definition: WorkflowDef,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await update_workflow(
        session,
        workflow_id=workflow_id,
        user_id=user.id,
        name=definition.name,
        definition=definition.model_dump(mode="json"),
    )
    if row is None:
        # 404 covers both "doesn't exist" and "exists but is a template / not mine" —
        # we don't leak which.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow not found or not editable")
    return _to_response(row)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    workflow_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    if not await delete_workflow(session, workflow_id=workflow_id, user_id=user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow not found or not deletable")
