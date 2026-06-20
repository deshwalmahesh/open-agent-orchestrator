"""P2e: the request_id correlation id crosses the queue boundary.

- start_run (queue mode) puts the bound request_id into the enqueued job args.
- worker.execute_run re-binds it to structlog contextvars for the job's duration.
"""
from __future__ import annotations

import uuid

import pytest
import structlog

from app.config import get_settings
from app.db import create_all, get_session_factory
from app.db.models import AgentDB, ChatDB, UserDB
from app.domain import AgentConfig


async def _make_chat(sf):
    async with sf() as s:
        user = UserDB(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@b.c",
                      hashed_password="x", is_active=True)
        s.add(user)
        await s.flush()
        cfg = AgentConfig.model_validate({
            "name": "Echo", "role": "assistant", "system_prompt": "x",
            "llm": {"provider": "openai", "base_url": "http://stub/v1",
                    "api_key": "stub", "model": "m"},
            "tools": [], "memory": {"type": "none"},
        }).model_dump(mode="json")
        agent = AgentDB(user_id=user.id, name="Echo", config=cfg)
        s.add(agent)
        await s.flush()
        chat = ChatDB(user_id=user.id, agent_id=agent.id)
        s.add(chat)
        await s.commit()
        return chat.id


@pytest.mark.asyncio
async def test_start_run_enqueues_request_id(monkeypatch):
    monkeypatch.setenv("RUN_EXECUTOR", "queue")
    get_settings.cache_clear()
    await create_all()
    sf = get_session_factory()
    chat_id = await _make_chat(sf)

    captured = {}

    class FakePool:
        async def enqueue_job(self, fn, *args, _job_id=None):
            captured["args"] = args
            return object()

    from app.services import run_service
    monkeypatch.setattr(run_service, "_ARQ_POOL", FakePool())

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="REQ-123")
    try:
        async with sf() as s:
            await run_service.start_run(s, chat_id=chat_id, user_text="hi", files=[])
    finally:
        structlog.contextvars.clear_contextvars()

    # request_id is the last positional arg (run_id, chat_id, text, files, request_id).
    assert captured["args"][-1] == "REQ-123"


@pytest.mark.asyncio
async def test_worker_rebinds_request_id(monkeypatch):
    seen = {}

    async def fake_execute(run_id, chat_id, user_text, files):
        seen.update(structlog.contextvars.get_contextvars())

    from app import worker
    monkeypatch.setattr(worker, "_execute", fake_execute)

    await worker.execute_run({}, str(uuid.uuid4()), str(uuid.uuid4()), "hi", [], "REQ-999")
    assert seen.get("request_id") == "REQ-999"
    # cleared after the job so it can't leak into the next one
    assert "request_id" not in structlog.contextvars.get_contextvars()
