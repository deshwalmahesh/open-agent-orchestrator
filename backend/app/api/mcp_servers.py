"""MCP server CRUD + live tool discovery."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.db.models import UserDB
from app.db.repos import create_mcp_server, delete_mcp_server, get_mcp_server, list_mcp_servers
from app.users import current_active_user

log = structlog.get_logger()

router = APIRouter(prefix="/mcp-servers", tags=["mcp"])


class MCPServerBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1)
    transport: str = Field(default="http", pattern="^(http|sse)$")
    headers: dict[str, str] = Field(default_factory=dict)


def _to_response(row) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "url": row.url,
        "transport": row.transport,
        "headers": row.headers,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(
    body: MCPServerBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await create_mcp_server(
        session, user_id=user.id, name=body.name, url=body.url,
        transport=body.transport, headers=body.headers,
    )
    return _to_response(row)


@router.get("")
async def list_mine(
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    return [_to_response(r) for r in await list_mcp_servers(session, user_id=user.id)]


@router.get("/{server_id}")
async def get_one(
    server_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await get_mcp_server(session, server_id=server_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mcp server not found")
    return _to_response(row)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    server_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> None:
    if not await delete_mcp_server(session, server_id=server_id, user_id=user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mcp server not found")


@router.get("/{server_id}/tools")
async def discover_tools(
    server_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[dict]:
    """Connect to MCP server, discover available tools, return names + descriptions."""
    row = await get_mcp_server(session, server_id=server_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mcp server not found")

    from langchain_mcp_adapters.client import MultiServerMCPClient

    try:
        client = MultiServerMCPClient({
            row.name: {"url": row.url, "transport": row.transport, "headers": row.headers},
        })
        tools = await client.get_tools()
    except Exception as exc:
        log.warning("mcp.discover.failed", server_id=str(server_id), error=str(exc))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"failed to connect: {exc}")

    return [{"name": t.name, "description": t.description or ""} for t in tools]
