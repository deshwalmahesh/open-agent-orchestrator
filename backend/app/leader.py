"""Redis single-instance leader lock — so exactly one replica runs a process-wide
singleton (Slack Socket Mode: N replicas each opening a socket = every message
processed N times).

Standard pattern: acquire with `SET key token NX EX ttl`; refresh/release are
Lua-fenced so a replica can only extend or delete a lease it actually owns (a
plain `EXPIRE`/`DEL` could clobber a lease another replica took over after our
TTL lapsed). On leader death the TTL lapses and a poller takes over.

# ponytail: single-Redis lock; brief double-leadership possible across a GC pause
# longer than the TTL margin. Fine for Slack; use Redlock/fencing if it ever guards
# something that must be strictly exclusive.
"""
from __future__ import annotations

from uuid import uuid4

import structlog

from app.redis_client import get_redis

log = structlog.get_logger()

# Extend TTL only if we still own the key (token matches).
_REFRESH = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
# Delete only if we own it.
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class Leader:
    def __init__(self, key: str, ttl: int = 30) -> None:
        self.key = key
        self.ttl = ttl
        self._token: str | None = None

    @property
    def held(self) -> bool:
        return self._token is not None

    async def acquire(self) -> bool:
        token = uuid4().hex
        if await get_redis().set(self.key, token, nx=True, ex=self.ttl):
            self._token = token
            return True
        return False

    async def refresh(self) -> bool:
        """Re-extend our lease. Returns False if we lost it (then we're no longer leader)."""
        if self._token is None:
            return False
        ok = bool(await get_redis().eval(_REFRESH, 1, self.key, self._token, self.ttl))
        if not ok:
            self._token = None
        return ok

    async def release(self) -> None:
        if self._token is not None:
            try:
                await get_redis().eval(_RELEASE, 1, self.key, self._token)
            finally:
                self._token = None
