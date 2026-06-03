"""Process-scoped AsyncRedisSaver factory. Caller owns the Redis client."""

from __future__ import annotations

import structlog
from langgraph.checkpoint.redis import AsyncRedisSaver
from redis.asyncio import Redis as AsyncRedis

from app.config import get_settings

log = structlog.get_logger()


async def build_checkpointer() -> tuple[AsyncRedisSaver, AsyncRedis]:
    """Connect to Redis and return (saver, client).

    Caller MUST `await client.aclose()` at shutdown. Raises whatever the Redis
    client raises if the connection fails — the lifespan handler decides
    whether to degrade or crash.
    """
    redis_url = get_settings().redis_url
    if not redis_url:
        raise RuntimeError("REDIS_URL not configured — checkpointer disabled")
    log.info("checkpointer.connecting", redis_url=redis_url)
    client = AsyncRedis.from_url(redis_url)
    # Round-trip ping forces the connection NOW so failure surfaces here, not
    # mid-request later.
    await client.ping()
    saver = AsyncRedisSaver(redis_client=client)
    await saver.asetup()
    log.info("checkpointer.ready")
    return saver, client
