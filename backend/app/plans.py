"""Per-plan limits — single source of truth for tier policy (concurrency / rate / quota).

ponytail: a static dict. Move to DB rows / Stripe product metadata when plans become
dynamic or billable; callers go through limits_for() so that swap is one-file.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanLimits:
    max_concurrent_runs: int  # in-flight runs per user; 0 = unlimited
    daily_tokens: int         # tokens/day per user; 0 = unlimited (enforced by 3c quota)


PLANS: dict[str, PlanLimits] = {
    "free": PlanLimits(max_concurrent_runs=1, daily_tokens=50_000),
    "paid": PlanLimits(max_concurrent_runs=10, daily_tokens=0),
}


def limits_for(plan: str | None) -> PlanLimits:
    """Limits for a plan name, defaulting to the most restrictive (free)."""
    return PLANS.get(plan or "free", PLANS["free"])
