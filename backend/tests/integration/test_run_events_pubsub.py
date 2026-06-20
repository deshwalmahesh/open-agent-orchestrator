"""P2a: RunEventEmitter publishes each event to its Redis channel so the SSE
endpoint in a DIFFERENT process (queue mode) can stream it live. Redis-gated."""
from __future__ import annotations

import json
import uuid

import pytest

from app.db import create_all, get_session_factory
from app.redis_client import get_redis
from app.runtime.events import RunEventEmitter, run_channel


@pytest.mark.asyncio
async def test_emitter_publishes_to_redis_channel():
    try:
        await get_redis().ping()
    except Exception:
        pytest.skip("no reachable Redis")

    await create_all()
    run_id = uuid.uuid4()

    sub = get_redis().pubsub()
    await sub.subscribe(run_channel(run_id))

    emitter = RunEventEmitter(run_id, get_session_factory())
    await emitter.emit("run.started", {"hello": "world"})

    received = None
    for _ in range(50):
        msg = await sub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if msg is not None:
            received = json.loads(msg["data"])
            break
    await sub.aclose()

    assert received is not None, "no message received on run channel"
    assert received["type"] == "run.started"
    assert received["seq"] == 1
    assert received["data"] == {"hello": "world"}
