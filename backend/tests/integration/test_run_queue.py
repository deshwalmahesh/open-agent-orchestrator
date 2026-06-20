"""F3: the run-executor config seam.

Two cases:
- Routing (no Redis): in "queue" mode, start_run enqueues to the arq pool with
  _job_id=run_id and does NOT execute inline (run stays "queued", no messages).
- End-to-end (gated on a reachable Redis): enqueue a real job and drain it with
  an arq burst worker; assert the run reaches a terminal state.
"""
from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select

from app.config import get_settings
from app.db import create_all, get_session_factory
from app.db.models import AgentDB, ChatDB, MessageDB, RunDB, UserDB
from app.domain import AgentConfig


def _agent_config() -> dict:
    return AgentConfig.model_validate({
        "name": "Echo", "role": "assistant", "system_prompt": "Reply briefly.",
        "llm": {"provider": "openai", "base_url": "http://stub.local/v1",
                "api_key": "stub", "model": "stub-model"},
        "tools": [], "memory": {"type": "none"},
    }).model_dump(mode="json")


async def _make_chat(sf):
    async with sf() as s:
        user = UserDB(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@b.c",
                      hashed_password="x", is_active=True)
        s.add(user)
        await s.flush()
        agent = AgentDB(user_id=user.id, name="Echo", config=_agent_config())
        s.add(agent)
        await s.flush()
        chat = ChatDB(user_id=user.id, agent_id=agent.id)
        s.add(chat)
        await s.commit()
        return chat.id


@pytest.mark.asyncio
async def test_queue_mode_enqueues_not_inline(monkeypatch):
    monkeypatch.setenv("RUN_EXECUTOR", "queue")
    get_settings.cache_clear()
    await create_all()
    sf = get_session_factory()
    chat_id = await _make_chat(sf)

    calls = []

    class FakePool:
        async def enqueue_job(self, fn, *args, _job_id=None):
            calls.append((fn, args, _job_id))
            return object()

    from app.services import run_service
    monkeypatch.setattr(run_service, "_ARQ_POOL", FakePool())

    async with sf() as s:
        run_id = await run_service.start_run(s, chat_id=chat_id, user_text="hi", files=[])

    # Enqueued with the right function + dedup id.
    assert len(calls) == 1
    fn, args, job_id = calls[0]
    assert fn == "execute_run"
    assert args[0] == str(run_id) and args[1] == str(chat_id)
    assert job_id == str(run_id)

    # NOT executed inline: still queued, no messages written.
    async with sf() as s:
        run = await s.get(RunDB, run_id)
        msgs = (await s.execute(
            select(MessageDB).where(MessageDB.run_id == run_id)
        )).scalars().all()
    assert run.status == "queued"
    assert msgs == []


async def _redis_reachable(url: str) -> bool:
    try:
        from redis.asyncio import Redis
        client = Redis.from_url(url)
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_queue_end_to_end_with_burst_worker(monkeypatch):
    """Real arq round-trip when Redis is up; skipped otherwise."""
    url = get_settings().redis_url
    if not await _redis_reachable(url):
        pytest.skip("no reachable Redis for arq end-to-end")

    from arq import create_pool
    from arq.connections import RedisSettings
    from arq.worker import Worker

    await create_all()
    sf = get_session_factory()
    chat_id = await _make_chat(sf)

    async def _stub(agent, messages, config, *, breaker_key=None):
        return {"messages": [HumanMessage(content="hi"), AIMessage(content="ok")]}

    from app.services import run_service
    monkeypatch.setattr(run_service, "invoke_with_breaker", _stub)
    from app.worker import execute_run

    settings = RedisSettings.from_dsn(url)
    pool = await create_pool(settings)
    monkeypatch.setattr(run_service, "_ARQ_POOL", pool)
    monkeypatch.setenv("RUN_EXECUTOR", "queue")
    get_settings.cache_clear()

    async with sf() as s:
        run_id = await run_service.start_run(s, chat_id=chat_id, user_text="hi", files=[])

    worker = Worker(functions=[execute_run], redis_settings=settings,
                    burst=True, poll_delay=0.1)
    await worker.async_run()
    await worker.close()
    await pool.aclose()

    async with sf() as s:
        run = await s.get(RunDB, run_id)
    assert run.status in ("succeeded", "failed")
