"""Per-user tool credential management. Users save API keys / config for tools they use."""
from __future__ import annotations

import asyncio
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.db.models import UserDB
from app.db.repos import delete_tool_config, get_tool_config, list_tool_configs, upsert_tool_config
from app.users import current_active_user

log = structlog.get_logger()

router = APIRouter(prefix="/tool-configs", tags=["tools"])


class ToolConfigBody(BaseModel):
    config: dict = Field(..., description="Tool-specific credentials, e.g. {\"api_key\": \"tvly-xxx\"}")


def _to_response(row) -> dict:
    return {
        "tool_name": row.tool_name,
        "config": row.config,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("")
async def list_mine(
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    return [_to_response(r) for r in await list_tool_configs(session, user_id=user.id)]


@router.put("/{tool_name}")
async def upsert(
    tool_name: str,
    body: ToolConfigBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await upsert_tool_config(
        session, user_id=user.id, tool_name=tool_name, config=body.config,
    )
    return _to_response(row)


@router.get("/{tool_name}")
async def get_one(
    tool_name: str,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await get_tool_config(session, user_id=user.id, tool_name=tool_name)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tool config not found")
    return _to_response(row)


@router.delete("/{tool_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    tool_name: str,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    if not await delete_tool_config(session, user_id=user.id, tool_name=tool_name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tool config not found")


@router.post("/{tool_name}/validate")
async def validate(
    tool_name: str,
    body: ToolConfigBody,
    user: Annotated[UserDB, Depends(current_active_user)],
) -> dict:
    """Test tool credentials before saving. Returns {ok: bool, error?: str}.
    Only web_search (Tavily) does a live round-trip; all other tools return ok=True."""
    if tool_name != "web_search":
        return {"ok": True}

    api_key = body.config.get("api_key", "").strip()
    if not api_key:
        return {"ok": False, "error": "API key is required"}
    try:
        from langchain_tavily import TavilySearch
        tool = TavilySearch(max_results=1, tavily_api_key=api_key)
        await asyncio.to_thread(tool.invoke, "test")
        return {"ok": True}
    except Exception as exc:
        # Log the exception class for diagnosability without echoing the raw message
        # (Tavily errors may contain the submitted key — never bounce it back to the client).
        log.warning("tool.validate.failed", tool=tool_name, exc_type=type(exc).__name__)
        return {"ok": False, "error": "Could not authenticate with Tavily — check the API key."}
