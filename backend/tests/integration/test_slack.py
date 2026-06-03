"""Slack inbound dispatch — pure logic test, no Socket Mode connection.

We test the handler `handle_slack_message` directly with a mock `say`. The Bolt
adapter just routes events into this function, so this covers the round-trip.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.db import get_session_factory
from app.db.models import AgentDB, UserDB
from app.integrations.channels.slack_adapter import handle_slack_message


def _event(user: str = "U_ALICE", text: str = "hi", channel: str = "D1", ts: str = "1.1") -> dict:
    return {"user": user, "text": text, "channel": channel, "ts": ts}


async def _attach_slack_id(user_email: str, slack_uid: str) -> None:
    """PATCH /users/me equivalent — direct DB write for setup brevity."""
    from sqlalchemy import select, update

    sf = get_session_factory()
    async with sf() as s:
        row = (await s.execute(select(UserDB).where(UserDB.email == user_email))).scalar_one()
        await s.execute(update(UserDB).where(UserDB.id == row.id).values(slack_user_id=slack_uid))
        await s.commit()


def test_unknown_slack_user_gets_help(client):
    """No-op for DB; bot replies with registration hint."""
    say = AsyncMock()
    asyncio.run(
        handle_slack_message(_event(user="U_UNKNOWN"), say, session_factory=get_session_factory())
    )
    say.assert_awaited_once()
    kwargs = say.await_args.kwargs
    assert kwargs["thread_ts"] == "1.1"
    # Friendly hint: tells user we don't recognise them and echoes their Slack ID
    # so they can self-link via the web UI.
    text = kwargs["text"].lower()
    assert "recognise" in text
    assert "u_unknown" in text


def test_known_user_no_agent_gets_hint(client, signup_and_login):
    """User exists + has slack_user_id set, but no agent yet."""
    signup_and_login("alice@example.com")
    asyncio.run(_attach_slack_id("alice@example.com", "U_ALICE"))

    say = AsyncMock()
    asyncio.run(handle_slack_message(_event(), say, session_factory=get_session_factory()))
    say.assert_awaited_once()
    assert "pipeline" in say.await_args.kwargs["text"].lower()


def test_known_user_with_agent_dispatches_run(client, signup_and_login, auth_header, sample_agent_config, monkeypatch):
    """Routes to start_run + posts reply. We mock start_run + wait_for_reply to
    keep this hermetic (no LLM dependency for the routing test)."""
    token = signup_and_login("alice@example.com")
    asyncio.run(_attach_slack_id("alice@example.com", "U_ALICE"))
    client.post("/agents", json=sample_agent_config(), headers=auth_header(token))

    captured: dict = {}

    async def fake_start_run(session, *, chat_id, user_text):
        captured["chat_id"] = chat_id
        captured["text"] = user_text
        from uuid import uuid4
        return uuid4()

    async def fake_wait_for_reply(run_id, *, timeout=60.0):
        return ("succeeded", "echoed: hi")

    monkeypatch.setattr("app.integrations.channels.slack_adapter.start_run", fake_start_run)
    monkeypatch.setattr(
        "app.integrations.channels.slack_adapter.wait_for_reply", fake_wait_for_reply
    )

    say = AsyncMock()
    asyncio.run(handle_slack_message(_event(), say, session_factory=get_session_factory()))

    assert captured["text"] == "hi"
    say.assert_awaited_once_with(thread_ts="1.1", text="echoed: hi")


def test_thread_reuses_same_chat(client, signup_and_login, auth_header, sample_agent_config, monkeypatch):
    """Two messages on the same thread_ts must land in the SAME ChatDB row."""
    token = signup_and_login("alice@example.com")
    asyncio.run(_attach_slack_id("alice@example.com", "U_ALICE"))
    client.post("/agents", json=sample_agent_config(), headers=auth_header(token))

    chat_ids: list = []

    async def fake_start_run(session, *, chat_id, user_text):
        chat_ids.append(chat_id)
        from uuid import uuid4
        return uuid4()

    async def fake_wait_for_reply(run_id, *, timeout=60.0):
        return ("succeeded", "ok")

    monkeypatch.setattr("app.integrations.channels.slack_adapter.start_run", fake_start_run)
    monkeypatch.setattr(
        "app.integrations.channels.slack_adapter.wait_for_reply", fake_wait_for_reply
    )

    say = AsyncMock()
    asyncio.run(handle_slack_message(_event(text="m1"), say, session_factory=get_session_factory()))
    asyncio.run(handle_slack_message(_event(text="m2"), say, session_factory=get_session_factory()))

    assert len(chat_ids) == 2
    assert chat_ids[0] == chat_ids[1]


def test_bot_messages_ignored(client):
    """Self-echo guard — events with bot_id must not call say at all."""
    say = AsyncMock()
    event = {**_event(), "bot_id": "B_SELF"}
    asyncio.run(handle_slack_message(event, say, session_factory=get_session_factory()))
    say.assert_not_awaited()
