"""F4: startup reconciler marks long-stale non-terminal runs as failed(INTERRUPTED),
but never touches a fresh in-flight run or an already-terminal one."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.db import create_all, get_session_factory
from app.db.models import ChatDB, RunDB, UserDB
from app.domain import utcnow
from app.services.run_service import reconcile_orphaned_runs


@pytest.mark.asyncio
async def test_reconcile_only_reaps_stale(monkeypatch):
    await create_all()
    sf = get_session_factory()

    async with sf() as s:
        user = UserDB(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@b.c",
                      hashed_password="x", is_active=True)
        s.add(user)
        await s.flush()
        chat = ChatDB(user_id=user.id, agent_id=None)
        s.add(chat)
        await s.flush()

        old = RunDB(chat_id=chat.id, status="running",
                    started_at=utcnow() - timedelta(hours=2))
        fresh = RunDB(chat_id=chat.id, status="running", started_at=utcnow())
        done = RunDB(chat_id=chat.id, status="succeeded",
                     started_at=utcnow() - timedelta(hours=2))
        s.add_all([old, fresh, done])
        await s.commit()
        old_id, fresh_id, done_id = old.id, fresh.id, done.id

    n = await reconcile_orphaned_runs()
    assert n == 1

    async with sf() as s:
        assert (await s.get(RunDB, old_id)).status == "failed"
        assert (await s.get(RunDB, old_id)).error_code == "INTERRUPTED"
        assert (await s.get(RunDB, fresh_id)).status == "running"   # in-flight, untouched
        assert (await s.get(RunDB, done_id)).status == "succeeded"  # terminal, untouched
