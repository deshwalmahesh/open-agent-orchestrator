"""Integration: _execute is idempotent under at-least-once redelivery.

Running the same run twice (as an arq worker would after a crash) must produce
exactly one user message, one agent reply, and one terminal state — never
double-inserts or double-billing.
"""
from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.db import create_all, get_session_factory
from app.db.models import AgentDB, ChatDB, MessageDB, RunDB, UserDB
from app.db.repos import create_run
from app.domain import AgentConfig


def _agent_config() -> dict:
    return AgentConfig.model_validate({
        "name": "Echo",
        "role": "assistant",
        "system_prompt": "Reply briefly.",
        "llm": {"provider": "openai", "base_url": "http://stub.local/v1",
                "api_key": "stub", "model": "stub-model"},
        "tools": [],
        "memory": {"type": "none"},
    }).model_dump(mode="json")


@pytest.mark.asyncio
async def test_execute_is_idempotent(monkeypatch):
    await create_all()
    sf = get_session_factory()

    async with sf() as s:
        user = UserDB(id=uuid.uuid4(), email="a@b.c", hashed_password="x", is_active=True)
        s.add(user)
        await s.flush()
        agent = AgentDB(user_id=user.id, name="Echo", config=_agent_config())
        s.add(agent)
        await s.flush()
        chat = ChatDB(user_id=user.id, agent_id=agent.id)
        s.add(chat)
        await s.commit()
        chat_id, agent_id = chat.id, agent.id

    async with sf() as s:
        run = await create_run(s, chat_id=chat_id, agent_id=agent_id)
        run_id = run.id

    async def _stub(agent, messages, config, *, breaker_key=None):
        return {"messages": [HumanMessage(content="hi"), AIMessage(content="ok")]}

    monkeypatch.setattr("app.services.run_service.invoke_with_breaker", _stub)
    from app.services.run_service import _execute

    # First delivery succeeds.
    await _execute(run_id, chat_id, "hi")
    # Duplicate delivery (worker crash + redelivery) must be a no-op.
    await _execute(run_id, chat_id, "hi")

    async with sf() as s:
        from sqlalchemy import select
        msgs = (await s.execute(
            select(MessageDB).where(MessageDB.chat_id == chat_id).order_by(MessageDB.ts)
        )).scalars().all()
        run_row = await s.get(RunDB, run_id)

    user_msgs = [m for m in msgs if m.sender == "user"]
    agent_msgs = [m for m in msgs if m.sender != "user"]
    assert len(user_msgs) == 1, f"duplicate user message: {[m.content for m in user_msgs]}"
    assert len(agent_msgs) == 1, f"duplicate agent reply: {[m.content for m in agent_msgs]}"
    assert run_row.status == "succeeded"
