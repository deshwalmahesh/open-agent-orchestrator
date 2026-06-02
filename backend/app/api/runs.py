"""Run observation: SSE event stream + DB-backed event history.

GET /runs/{id}/events — SSE: replays persisted backlog (seq > after_seq), then drains
live queue until the run ends (sentinel None on the in-process emitter queue).
SSE auth via `?token=<jwt>` query param (EventSource can't send headers).
"""
from __future__ import annotations

import asyncio
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.db import get_async_session
from app.db.models import UserDB
from app.db.repos import get_run, list_events
from app.runtime.events import EMITTERS
from app.users import UserManager, current_active_user, get_jwt_strategy

router = APIRouter(prefix="/runs", tags=["runs"])


async def _user_from_header_or_param(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> UserDB:
    """Authenticate via Authorization header OR ?token= query param.
    EventSource can't send headers, so the frontend passes ?token=<jwt>."""
    token = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
    if not token:
        token = request.query_params.get("token")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing auth")
    strategy = get_jwt_strategy()
    user_db = SQLAlchemyUserDatabase(session, UserDB)
    user_manager = UserManager(user_db)
    user = await strategy.read_token(token, user_manager)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    return user


def _serialize(event_type: str, seq: int, data: dict) -> dict:
    return {"event": event_type, "id": str(seq), "data": json.dumps(data)}


@router.get("/{run_id}/events")
async def stream_events(
    run_id: UUID,
    user: Annotated[UserDB, Depends(_user_from_header_or_param)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
    after_seq: int = Query(0, ge=0),
) -> EventSourceResponse:
    if await get_run(session, run_id=run_id, user_id=user.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")

    async def gen():
        # Backlog from durable store.
        backlog = await list_events(session, run_id=run_id, after_seq=after_seq)
        last_seq = after_seq
        for ev in backlog:
            yield _serialize(ev.type, ev.seq, ev.data)
            last_seq = ev.seq

        # Live drain — only if the run is still in-flight.
        emitter = EMITTERS.get(run_id)
        if emitter is None:
            return
        while True:
            try:
                ev = await asyncio.wait_for(emitter.queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": "{}"}
                continue
            if ev is None:  # sentinel — run finished
                return
            if ev.seq <= last_seq:  # backlog already covered this
                continue
            yield _serialize(ev.type, ev.seq, ev.data)
            last_seq = ev.seq

    return EventSourceResponse(gen())


@router.get("/{run_id}")
async def get_one(
    run_id: UUID,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    row = await get_run(session, run_id=run_id, user_id=user.id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return {
        "id": str(row.id),
        "chat_id": str(row.chat_id),
        "agent_id": str(row.agent_id) if row.agent_id else None,
        "status": row.status,
        "started_at": row.started_at.isoformat(),
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "total_tokens": row.total_tokens,
        "total_cost": row.total_cost,
        "error": row.error,
    }
