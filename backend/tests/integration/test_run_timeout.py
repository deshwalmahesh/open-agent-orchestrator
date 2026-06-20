"""F5: per-run wall-clock cap. A run that exceeds run_timeout_s finishes as
failed(RUN_TIMEOUT) instead of hanging forever. Works in inline mode (no arq)."""
from __future__ import annotations

import asyncio
import uuid

import pytest
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


@pytest.mark.asyncio
async def test_run_wall_clock_timeout(monkeypatch):
    monkeypatch.setenv("RUN_TIMEOUT_S", "1")  # tight cap for the test
    get_settings.cache_clear()
    await create_all()
    sf = get_session_factory()

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
        chat_id, agent_id = chat.id, agent.id

    async def _hang(agent, messages, config, *, breaker_key=None):
        await asyncio.sleep(30)  # never returns within the 1s cap

    monkeypatch.setattr("app.services.run_service.invoke_with_breaker", _hang)
    from app.services.run_service import _execute

    async with sf() as s:
        from app.db.repos import create_run
        run = await create_run(s, chat_id=chat_id, agent_id=agent_id)
        run_id = run.id

    await _execute(run_id, chat_id, "hi")

    async with sf() as s:
        run_row = await s.get(RunDB, run_id)
        msgs = (await s.execute(
            select(MessageDB).where(MessageDB.run_id == run_id)
        )).scalars().all()
    assert run_row.status == "failed"
    assert run_row.error_code == "RUN_TIMEOUT"
    assert run_row.error  # user-facing message
    # User turn was still persisted before the LLM call (debuggable history).
    assert any(m.sender == "user" for m in msgs)
