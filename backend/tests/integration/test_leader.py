"""P2b: Redis leader lock — only one holder at a time; refresh/release are fenced
to the owning token. Redis-gated."""
from __future__ import annotations

import uuid

import pytest

from app.leader import Leader
from app.redis_client import get_redis


@pytest.mark.asyncio
async def test_leader_mutual_exclusion_and_fencing():
    try:
        await get_redis().ping()
    except Exception:
        pytest.skip("no reachable Redis")

    key = f"test:leader:{uuid.uuid4().hex}"
    a, b = Leader(key, ttl=5), Leader(key, ttl=5)

    assert await a.acquire() is True      # first wins
    assert await b.acquire() is False     # contended — b loses
    assert await a.refresh() is True       # owner can extend
    assert b.held is False

    await a.release()
    assert await a.refresh() is False      # released → no longer owner
    assert await b.acquire() is True       # freed → b takes over
    await b.release()
