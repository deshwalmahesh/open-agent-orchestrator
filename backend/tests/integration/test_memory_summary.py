"""Rolling-summary memory: threshold-cross folds oldest M messages into ChatDB.summary.

`_summarize` is monkeypatched to a deterministic string so we don't pay live-LLM cost
on a behaviour test — the test is about WHEN summarisation triggers and what state
mutates, not about the LLM's prose quality.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4


from app.db import get_session_factory
from app.db.models import AgentDB, ChatDB, MessageDB, UserDB
from app.domain import AgentConfig, LLMConfig, MemoryConfig
from app.services.run_service import _resolve_context


def _agent_with_memory(window: int, threshold: int) -> AgentConfig:
    return AgentConfig(
        name="t", role="t", system_prompt="x",
        llm=LLMConfig(base_url="http://x.local/v1", model="m"),
        memory=MemoryConfig(type="summary", window=window, summary_threshold=threshold),
    )


async def _seed_user_chat_with_n_messages(n: int) -> tuple[UserDB, ChatDB]:
    sf = get_session_factory()
    async with sf() as s:
        user = UserDB(email=f"u-{uuid4().hex[:8]}@x.com", hashed_password="x", is_active=True)
        s.add(user)
        await s.flush()
        agent = AgentDB(user_id=user.id, name="a", config={})
        s.add(agent)
        await s.flush()
        chat = ChatDB(user_id=user.id, agent_id=agent.id, channel="web")
        s.add(chat)
        await s.flush()
        for i in range(n):
            s.add(MessageDB(
                chat_id=chat.id, run_id=None,
                sender="user" if i % 2 == 0 else str(agent.id),
                content=f"turn {i}",
            ))
        await s.commit()
        await s.refresh(chat)
        return user, chat


def test_summary_not_triggered_below_threshold(client, monkeypatch):
    """5 messages, n=2 + m=3 → unsummarized (5) <= n+m (5) → no fold."""
    calls = {"n": 0}

    async def fake_summarize(*args, **kwargs):
        calls["n"] += 1
        return "S"

    monkeypatch.setattr("app.services.run_service._summarize", fake_summarize)

    cfg = _agent_with_memory(window=2, threshold=3)

    async def go():
        _, chat = await _seed_user_chat_with_n_messages(5)
        sf = get_session_factory()
        async with sf() as s:
            chat_in_session = await s.get(ChatDB, chat.id)
            summary, verbatim = await _resolve_context(s, chat_in_session, cfg)
        return summary, verbatim, chat

    summary, verbatim, chat = asyncio.run(go())
    assert calls["n"] == 0  # no summariser call
    assert summary == ""
    assert len(verbatim) == 5  # all 5 fed through verbatim
    assert chat.summary == "" and chat.summary_count == 0


def test_summary_triggers_when_unsummarized_exceeds_n_plus_m(client, monkeypatch):
    """6 messages, n=2 + m=3 → unsummarized (6) > n+m (5) → fold oldest 3."""
    async def fake_summarize(llm_cfg, prior, batch):
        return f"SUMMARY({len(batch)})"

    monkeypatch.setattr("app.services.run_service._summarize", fake_summarize)

    cfg = _agent_with_memory(window=2, threshold=3)

    async def go():
        _, chat = await _seed_user_chat_with_n_messages(6)
        sf = get_session_factory()
        async with sf() as s:
            chat_in_session = await s.get(ChatDB, chat.id)
            summary, verbatim = await _resolve_context(s, chat_in_session, cfg)
            await s.refresh(chat_in_session)
            return summary, verbatim, chat_in_session

    summary, verbatim, chat_row = asyncio.run(go())
    assert summary == "SUMMARY(3)"
    assert len(verbatim) == 3  # 6 - 3 folded = 3 remaining verbatim
    assert chat_row.summary == "SUMMARY(3)"
    assert chat_row.summary_count == 3


def test_second_summary_call_folds_into_prior(client, monkeypatch):
    """After one fold (summary_count=3), add 6 more → trigger again, summariser sees prior."""
    received_prior: list[str] = []

    async def fake_summarize(llm_cfg, prior, batch):
        received_prior.append(prior)
        return f"S({prior!r}+{len(batch)})"

    monkeypatch.setattr("app.services.run_service._summarize", fake_summarize)

    cfg = _agent_with_memory(window=2, threshold=3)

    async def go():
        # First trigger: 6 messages, fold 3.
        _, chat = await _seed_user_chat_with_n_messages(6)
        sf = get_session_factory()
        async with sf() as s:
            chat_row = await s.get(ChatDB, chat.id)
            await _resolve_context(s, chat_row, cfg)
            # Add 6 more messages (now 12 total, summary_count=3 → unsummarized=9 > 5).
            for i in range(6, 12):
                s.add(MessageDB(
                    chat_id=chat.id, run_id=None,
                    sender="user", content=f"turn {i}",
                ))
            await s.commit()
            await s.refresh(chat_row)
            # Second trigger.
            await _resolve_context(s, chat_row, cfg)
            await s.refresh(chat_row)
            return chat_row

    chat_row = asyncio.run(go())
    assert len(received_prior) == 2
    assert received_prior[0] == ""  # first fold sees empty prior
    assert "S(" in received_prior[1]  # second fold sees previous summary as prior
    assert chat_row.summary_count == 6
