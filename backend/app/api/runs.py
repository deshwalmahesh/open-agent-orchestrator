"""Run observation: SSE event stream + DB-backed event history.

GET /runs/{id}/events — SSE: replays persisted backlog (seq > after_seq), then drains
live queue until the run ends (sentinel None on the in-process emitter queue).
SSE auth via `?token=<jwt>` query param (EventSource can't send headers).
"""
from __future__ import annotations

import asyncio
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.db import get_async_session
from app.db.models import UserDB
from app.db.repos import get_run, list_events, upsert_feedback
from app.observability import record_score
from app.redis_client import get_redis
from app.runtime.events import EMITTERS, run_channel
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
        # 1) Durable backlog (works regardless of which process ran the job).
        backlog = await list_events(session, run_id=run_id, after_seq=after_seq)
        last_seq = after_seq
        for ev in backlog:
            yield _serialize(ev.type, ev.seq, ev.data)
            last_seq = ev.seq
            if ev.type == "run.finished":
                return  # already terminal — nothing live to wait for

        # 2a) Same-process live drain (inline mode): the emitter is in THIS process.
        emitter = EMITTERS.get(run_id)
        if emitter is not None:
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
            return

        # 2b) Cross-process live drain (queue mode): the run executes in a worker, so
        # subscribe to its Redis channel. If Redis is unreachable and the run isn't
        # local, there's nothing to stream live — backlog already flushed, so return.
        try:
            pubsub = get_redis().pubsub()
            await pubsub.subscribe(run_channel(run_id))
        except Exception:
            return
        try:
            # Catch-up: events written between the backlog read and subscribe would
            # otherwise be missed (and a finished run would hang the stream).
            for ev in await list_events(session, run_id=run_id, after_seq=last_seq):
                yield _serialize(ev.type, ev.seq, ev.data)
                last_seq = ev.seq
                if ev.type == "run.finished":
                    return
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
                if msg is None:
                    yield {"event": "ping", "data": "{}"}
                    continue
                d = json.loads(msg["data"])
                if d["seq"] <= last_seq:
                    continue
                yield _serialize(d["type"], d["seq"], d["data"])
                last_seq = d["seq"]
                if d["type"] == "run.finished":
                    return
        finally:
            await pubsub.aclose()

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
        "error_code": row.error_code,
        "tool_calls": row.tool_calls,
    }


class FeedbackBody(BaseModel):
    rating: Literal["up", "down"]
    comment: str | None = None


@router.post("/{run_id}/feedback", status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    run_id: UUID,
    body: FeedbackBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Thumbs up/down (+ optional comment) on a run. Owner-checked; one per (user, run)."""
    if await get_run(session, run_id=run_id, user_id=user.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    row = await upsert_feedback(
        session, user_id=user.id, run_id=run_id, rating=body.rating, comment=body.comment
    )
    # Mirror to a Langfuse score (best-effort; DB row above is the source of truth).
    record_score(run_id, name="user-thumbs", value=1 if body.rating == "up" else 0,
                 comment=body.comment)
    return {"id": str(row.id), "rating": row.rating}
