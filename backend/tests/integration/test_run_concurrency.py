"""3b: per-plan concurrency cap. count_active_runs counts non-terminal runs via the
chat→user join; _enforce_concurrency rejects once a user is at their plan's cap."""
from __future__ import annotations

import uuid

import pytest

from app.db import create_all, get_session_factory
from app.db.models import ChatDB, RunDB, UserDB
from app.db.repos import count_active_runs
from app.services.run_service import ConcurrencyLimitExceeded, _enforce_concurrency


async def _user_with_runs(plan: str, statuses: list[str]):
    sf = get_session_factory()
    async with sf() as s:
        user = UserDB(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@b.c",
                      hashed_password="x", is_active=True, plan=plan)
        s.add(user)
        await s.flush()
        chat = ChatDB(user_id=user.id, agent_id=None)
        s.add(chat)
        await s.flush()
        for st in statuses:
            s.add(RunDB(chat_id=chat.id, status=st))
        await s.commit()
        return user.id


@pytest.mark.asyncio
async def test_count_active_runs_ignores_terminal():
    await create_all()
    uid = await _user_with_runs("free", ["queued", "running", "succeeded", "failed"])
    sf = get_session_factory()
    async with sf() as s:
        assert await count_active_runs(s, user_id=uid) == 2  # only queued + running


@pytest.mark.asyncio
async def test_free_plan_rejects_at_cap():
    await create_all()
    uid = await _user_with_runs("free", ["running"])  # free cap = 1, already 1 active
    sf = get_session_factory()
    async with sf() as s:
        with pytest.raises(ConcurrencyLimitExceeded):
            await _enforce_concurrency(s, uid, "free")


@pytest.mark.asyncio
async def test_paid_plan_allows_more():
    await create_all()
    uid = await _user_with_runs("paid", ["running", "running"])  # paid cap = 10
    sf = get_session_factory()
    async with sf() as s:
        await _enforce_concurrency(s, uid, "paid")  # 2 < 10 → no raise
