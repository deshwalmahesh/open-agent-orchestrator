"""Shared coordination Redis client.

One plain `redis.asyncio` client for app-level coordination across replicas:
SSE pub/sub, WhatsApp dedup, Slack leader lock, load-shedding, rate limits.

Deliberately SEPARATE from the LangGraph checkpointer client (`runtime/checkpointer`),
which requires redis-stack/RediSearch (`FT.*`). This one issues only plain commands
(PUBLISH/SUBSCRIBE, SET NX EX, INCR, LLEN) so it works on any Redis, including a
vanilla `redis:alpine`. Lazy singleton — `Redis.from_url` opens no socket until the
first command, so importing/calling this is safe even when Redis is down.
"""
from __future__ import annotations

import structlog
from redis.asyncio import Redis

from app.config import get_settings

log = structlog.get_logger()

_redis: Redis | None = None


def get_redis() -> Redis:
    """Process-wide singleton. decode_responses=True so pub/sub payloads are str."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def aclose_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
