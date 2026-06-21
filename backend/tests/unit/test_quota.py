"""Phase 3c: per-user daily token quota + per-model cost accounting."""
from __future__ import annotations

import uuid

import pytest

from app import quota
from app.quota import QuotaExceeded, cost_for, enforce_quota


# --- cost_for: longest-prefix match + safe fallback ---

def test_cost_for_longest_prefix_wins():
    # gpt-4o-mini must NOT resolve to the pricier gpt-4o.
    c = cost_for("gpt-4o-mini-2024-07-18", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    assert c == round(0.15 + 0.60, 6)


def test_cost_for_unknown_model_uses_default_not_zero():
    c = cost_for("some-self-hosted-llm", {"input_tokens": 1_000_000, "output_tokens": 0})
    assert c == 1.00  # _DEFAULT_PRICE input leg, never silently 0


def test_cost_for_zero_usage_is_zero():
    assert cost_for("gpt-4o", {"input_tokens": 0, "output_tokens": 0}) == 0.0


# --- enforce_quota: cap logic (counter mocked, no Redis needed) ---

@pytest.mark.asyncio
async def test_enforce_quota_blocks_over_cap(monkeypatch):
    monkeypatch.setattr(quota, "usage_today", lambda uid: _async(50_000))
    with pytest.raises(QuotaExceeded):
        await enforce_quota(uuid.uuid4(), "free")  # free cap = 50_000


@pytest.mark.asyncio
async def test_enforce_quota_allows_under_cap(monkeypatch):
    monkeypatch.setattr(quota, "usage_today", lambda uid: _async(49_999))
    await enforce_quota(uuid.uuid4(), "free")  # must not raise


@pytest.mark.asyncio
async def test_enforce_quota_unlimited_plan_never_blocks(monkeypatch):
    # paid cap = 0 (unlimited) → usage_today must not even be consulted.
    monkeypatch.setattr(quota, "usage_today", lambda uid: _async(10**9))
    await enforce_quota(uuid.uuid4(), "paid")


async def _async(v):
    return v


# --- Redis round-trip for the daily counter (skips if Redis is down) ---

@pytest.mark.asyncio
async def test_add_usage_then_read(monkeypatch):
    from app.redis_client import get_redis
    try:
        await get_redis().ping()
    except Exception:
        pytest.skip("no Redis")
    uid = uuid.uuid4()
    assert await quota.usage_today(uid) == 0
    await quota.add_usage(uid, 1200)
    await quota.add_usage(uid, 300)
    assert await quota.usage_today(uid) == 1500


# --- HTTP boundary: over-quota → 429 ---

def test_post_message_over_quota_returns_429(
    client, signup_and_login, auth_header, sample_agent_config, monkeypatch
):
    async def _raise(user_id, plan):
        raise QuotaExceeded(used=60_000, cap=50_000)
    monkeypatch.setattr("app.services.run_service.enforce_quota", _raise)

    token = signup_and_login()
    agent_id = client.post("/agents", json=sample_agent_config("Q"),
                           headers=auth_header(token)).json()["id"]
    client.post(f"/agents/{agent_id}/deploy", headers=auth_header(token))
    chat_id = client.post("/chats", json={"agent_id": agent_id},
                          headers=auth_header(token)).json()["id"]

    r = client.post(f"/chats/{chat_id}/messages", json={"text": "hi"}, headers=auth_header(token))
    assert r.status_code == 429
    assert "daily tokens" in r.json()["detail"]
