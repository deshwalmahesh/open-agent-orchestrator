"""Slack bot configuration API.

Allows users to configure Slack tokens from the UI instead of editing .env.
The first user to POST /slack/connect becomes the platform-level bot owner;
their tokens are saved to UserDB and the adapter is (re)started live.
"""
from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.db.models import AgentDB, UserDB
from app.db.repos import get_agent
from app.users import current_active_user

router = APIRouter(prefix="/slack", tags=["slack"])


class SlackConnectBody(BaseModel):
    bot_token: str   # xoxb-...
    app_token: str   # xapp-...
    agent_id: str | None = None  # if set, this pipeline becomes the single Slack-active one


class SlackActiveBody(BaseModel):
    agent_id: str


async def _apply_single_slack_binding(
    session: AsyncSession, *, user_id: UUID, active_agent_id: UUID
) -> None:
    """Enforce one-active-pipeline-at-a-time for Slack: remove the slack ChannelBinding
    from every other agent the user owns, and add it to `active_agent_id`. Idempotent."""
    rows = (await session.execute(
        select(AgentDB).where(AgentDB.user_id == user_id)
    )).scalars().all()
    for row in rows:
        cfg = dict(row.config or {})
        channels = list(cfg.get("channels") or [])
        non_slack = [c for c in channels if c.get("channel") != "slack"]
        if row.id == active_agent_id:
            new_channels = [*non_slack, {"channel": "slack", "external_id": ""}]
        else:
            new_channels = non_slack
        if new_channels != channels:
            cfg["channels"] = new_channels
            row.config = cfg
    await session.commit()


async def _active_slack_agent_id(session: AsyncSession, *, user_id: UUID) -> UUID | None:
    """Return the id of the user's pipeline that holds the slack ChannelBinding, if any."""
    rows = (await session.execute(
        select(AgentDB).where(AgentDB.user_id == user_id)
    )).scalars().all()
    for row in rows:
        for c in (row.config or {}).get("channels") or []:
            if c.get("channel") == "slack":
                return row.id
    return None


@router.get("/status")
async def status(
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    # Per-user: this user is "connected" iff they own the saved tokens.
    # Single-owner platform bot — POST /slack/connect clears other users' tokens.
    connected = bool(user.slack_bot_token and user.slack_app_token)
    active = await _active_slack_agent_id(session, user_id=user.id)
    return {"connected": connected, "active_agent_id": str(active) if active else None}


@router.post("/connect")
async def connect(
    body: SlackConnectBody,
    request: Request,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Save tokens to the user record and (re)start the global Slack adapter live.

    If `agent_id` is provided, that pipeline becomes the single Slack-active one for
    this user — the slack ChannelBinding is cleared from every other agent and added
    to this one atomically."""
    # Single-owner platform bot: clear any other user's saved tokens before
    # claiming ownership. Prevents stale "Connected" UI for previous owners.
    await session.execute(
        update(UserDB)
        .where(UserDB.id != user.id, UserDB.slack_bot_token.isnot(None))
        .values(slack_bot_token=None, slack_app_token=None)
    )
    user.slack_bot_token = body.bot_token
    user.slack_app_token = body.app_token
    await session.commit()

    # Single-active-pipeline binding swap. Reject Draft pipelines — they can't
    # be used in chat or Slack until explicitly deployed.
    if body.agent_id:
        try:
            agent_uuid = UUID(body.agent_id)
        except ValueError:
            agent_uuid = None  # malformed — skip the swap (tokens still saved)
        if agent_uuid is not None:
            target = await get_agent(session, agent_id=agent_uuid, user_id=user.id)
            if target is None:
                raise HTTPException(status_code=404, detail="agent not found")
            if target.deployed_at is None:
                raise HTTPException(status_code=400, detail="pipeline is in Draft — deploy it before binding to Slack")
            await _apply_single_slack_binding(session, user_id=user.id, active_agent_id=agent_uuid)

    # Tear down existing adapter if running
    existing = getattr(request.app.state, "slack", None)
    existing_task = getattr(request.app.state, "slack_task", None)
    if existing is not None:
        try:
            await existing.stop()
        except Exception:
            pass
    if existing_task is not None:
        existing_task.cancel()

    # Start new adapter with provided tokens
    from app.integrations.channels.slack_adapter import SlackAdapter
    adapter = SlackAdapter(body.bot_token, body.app_token)
    request.app.state.slack = adapter
    request.app.state.slack_task = asyncio.create_task(adapter.start())

    active = await _active_slack_agent_id(session, user_id=user.id)
    return {"connected": True, "active_agent_id": str(active) if active else None}


@router.post("/active")
async def set_active(
    body: SlackActiveBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Switch which pipeline owns the Slack binding for this user. No token
    change, no adapter restart — just swaps the ChannelBinding atomically."""
    try:
        agent_uuid = UUID(body.agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid agent_id")
    target = await get_agent(session, agent_id=agent_uuid, user_id=user.id)
    if target is None:
        raise HTTPException(status_code=404, detail="agent not found")
    if target.deployed_at is None:
        raise HTTPException(status_code=400, detail="pipeline is in Draft — deploy it before binding to Slack")
    await _apply_single_slack_binding(session, user_id=user.id, active_agent_id=agent_uuid)
    active = await _active_slack_agent_id(session, user_id=user.id)
    return {"active_agent_id": str(active) if active else None}


@router.post("/disconnect")
async def disconnect(
    request: Request,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    user.slack_bot_token = None
    user.slack_app_token = None
    await session.commit()

    adapter = getattr(request.app.state, "slack", None)
    task = getattr(request.app.state, "slack_task", None)
    if adapter is not None:
        try:
            await adapter.stop()
        except Exception:
            pass
    if task is not None:
        task.cancel()
    request.app.state.slack = None
    request.app.state.slack_task = None

    return {"connected": False}
