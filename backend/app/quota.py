"""Per-user daily token quota + per-model cost accounting.

Quota: a Redis daily counter per user (`quota:tokens:{user_id}:{YYYYMMDD}`, ~2-day TTL).
Checked pre-run (reject when already over the plan's `daily_tokens` cap) and incremented
at finalize. ponytail: the check is "already over cap", so the run that crosses the
threshold still completes — a guardrail, not a hard ceiling, same as the concurrency cap.
An atomic reserve-then-refund only if strict spend enforcement is ever required.

Cost: a static per-model price table (USD per 1M tokens). `cost_for()` turns a run's
token usage into `total_cost` at finalize. Move to Stripe product metadata / a DB table
when billing goes dynamic; callers go through these functions so that swap is one file.
"""
from __future__ import annotations

import structlog

from app.domain import utcnow
from app.plans import limits_for
from app.redis_client import get_redis

log = structlog.get_logger()

# USD per 1M tokens, (input, output). Longest matching prefix wins, so "gpt-4o-mini-..."
# resolves to gpt-4o-mini, not gpt-4o. Unknown models fall back to _DEFAULT_PRICE so
# cost is never silently 0 for a model we forgot to list (self-hosted/vLLM included).
_PRICES_PER_M: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "o3": (2.00, 8.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus-4": (15.00, 75.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
}
_DEFAULT_PRICE = (1.00, 3.00)  # conservative fallback for unpriced / self-hosted models

_QUOTA_TTL_S = 60 * 60 * 48  # 2 days — comfortably spans the UTC day boundary


class QuotaExceeded(Exception):
    """User is over their plan's daily token cap (caller → 429)."""

    def __init__(self, used: int, cap: int) -> None:
        self.used, self.cap = used, cap
        super().__init__(f"daily tokens {used} >= cap {cap}")


def cost_for(model: str, usage: dict) -> float:
    """USD cost of a run's token usage, via longest-prefix match on the price table."""
    m = (model or "").lower()
    inp, out = _DEFAULT_PRICE
    for prefix in sorted(_PRICES_PER_M, key=len, reverse=True):
        if m.startswith(prefix):
            inp, out = _PRICES_PER_M[prefix]
            break
    cost = (usage.get("input_tokens", 0) * inp + usage.get("output_tokens", 0) * out) / 1_000_000
    return round(cost, 6)


def _day_key(user_id) -> str:
    return f"quota:tokens:{user_id}:{utcnow():%Y%m%d}"


async def usage_today(user_id) -> int:
    """Tokens this user has spent today (0 on none / Redis unreachable — never blocks)."""
    try:
        val = await get_redis().get(_day_key(user_id))
        return int(val) if val else 0
    except Exception as exc:  # noqa: BLE001 — quota is best-effort, never fail on Redis
        log.warning("quota.read_failed", user_id=str(user_id), error=str(exc))
        return 0


async def add_usage(user_id, tokens: int) -> None:
    """Add to today's counter. Best-effort: a metering blip must not fail a finished run."""
    if tokens <= 0:
        return
    try:
        r = get_redis()
        key = _day_key(user_id)
        await r.incrby(key, tokens)
        await r.expire(key, _QUOTA_TTL_S)
    except Exception as exc:  # noqa: BLE001
        log.warning("quota.incr_failed", user_id=str(user_id), error=str(exc))


async def enforce_quota(user_id, plan: str | None) -> None:
    """Raise QuotaExceeded if the user is already at/over their plan's daily token cap."""
    cap = limits_for(plan).daily_tokens
    if cap <= 0:  # unlimited plan
        return
    used = await usage_today(user_id)
    if used >= cap:
        log.info("run.quota_rejected", user_id=str(user_id), used=used, cap=cap)
        raise QuotaExceeded(used, cap)
