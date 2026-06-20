"""Per-run event emitter.

Each emit:
  1. monotonic seq
  2. INSERT into run_events (durable replay)
  3. put on asyncio.Queue (live SSE drain)

A module-level EMITTERS registry lets the SSE endpoint subscribe to live runs
by run_id. Removed on run completion to avoid leaks.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.db.repos import insert_event
from app.domain import EventType, RunEvent, utcnow
from app.redis_client import get_redis

log = structlog.get_logger()


def run_channel(run_id: UUID) -> str:
    return f"run:{run_id}"


class RunEventEmitter:
    def __init__(
        self, run_id: UUID, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        self.run_id = run_id
        self._session_factory = session_factory
        self._seq = 0
        self.queue: asyncio.Queue[RunEvent | None] = asyncio.Queue()

    async def emit(self, event_type: EventType, data: dict[str, Any] | None = None) -> None:
        self._seq += 1
        payload = data or {}
        event = RunEvent(run_id=self.run_id, seq=self._seq, ts=utcnow(), type=event_type, data=payload)
        # DB insert is authoritative + the durable replay source.
        async with self._session_factory() as session:
            await insert_event(
                session, run_id=self.run_id, seq=self._seq, event_type=event_type, data=payload
            )
        # Cross-process live fan-out: the run executes in the worker process, but the
        # SSE endpoint lives in the API process — publish so it can stream live. Best
        # effort: if Redis is down the DB backlog still carries everything on replay.
        try:
            await get_redis().publish(
                run_channel(self.run_id),
                json.dumps({"seq": self._seq, "type": event_type, "data": payload}),
            )
        except Exception as exc:
            log.warning("run.event.publish_failed", run_id=str(self.run_id), error=str(exc))
        # Same-process live drain (inline mode).
        await self.queue.put(event)
        log.debug("run.event", run_id=str(self.run_id), seq=self._seq, type=event_type)

    async def close(self) -> None:
        """Signal SSE subscribers that the run is over so they can drain and exit."""
        await self.queue.put(None)


EMITTERS: dict[UUID, RunEventEmitter] = {}
