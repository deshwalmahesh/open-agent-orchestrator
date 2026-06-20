"""WhatsApp webhook dispatch — pure logic tests, no real Twilio connection.

Tests the handler `handle_whatsapp_message` and the webhook endpoint directly.
Twilio REST calls are mocked — only routing/dispatch logic is under test.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4


from app.db import get_session_factory
from app.db.models import UserDB
from app.integrations.channels.whatsapp_adapter import handle_whatsapp_message, WhatsAppAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_whatsapp_user(email: str, account_sid: str = "AC_TEST_123") -> None:
    """Write Twilio creds directly to the user row."""
    from sqlalchemy import select, update

    sf = get_session_factory()
    async with sf() as s:
        row = (await s.execute(select(UserDB).where(UserDB.email == email))).scalar_one()
        await s.execute(
            update(UserDB).where(UserDB.id == row.id).values(
                whatsapp_account_sid=account_sid,
                whatsapp_auth_token="test_auth_token",
                whatsapp_from_number="whatsapp:+14155238886",
            )
        )
        await s.commit()


def _make_adapter(account_sid: str = "AC_TEST_123") -> WhatsAppAdapter:
    """Build a WhatsAppAdapter with a mocked Twilio client."""
    adapter = object.__new__(WhatsAppAdapter)
    adapter.account_sid = account_sid
    adapter.from_number = "whatsapp:+14155238886"
    adapter.send_message = AsyncMock(return_value=["SM_FAKE"])
    adapter.validate_signature = lambda url, params, sig: True
    return adapter


def _create_and_deploy(client, token: str, auth_header, sample_agent_config, **overrides) -> str:
    """Create an agent AND deploy it (WhatsApp adapter only routes to deployed agents)."""
    hdrs = auth_header(token)
    resp = client.post("/agents", json=sample_agent_config(**overrides), headers=hdrs)
    agent_id = resp.json()["id"]
    resp = client.post(f"/agents/{agent_id}/deploy", headers=hdrs)
    assert resp.status_code == 200, resp.text
    return agent_id


# ---------------------------------------------------------------------------
# Tests: handle_whatsapp_message (pure dispatch logic)
# ---------------------------------------------------------------------------


def test_unknown_account_sid_is_noop(client):
    """Unknown AccountSid → handler returns silently (no crash, no reply)."""
    adapter = _make_adapter("AC_UNKNOWN")
    asyncio.run(
        handle_whatsapp_message(
            "AC_UNKNOWN", "whatsapp:+1234567890", "hello", "John",
            adapter=adapter, session_factory=get_session_factory(),
        )
    )
    adapter.send_message.assert_not_awaited()


def test_known_user_no_agent_sends_hint(client, signup_and_login):
    """User exists with Twilio creds but no deployed pipeline → sends hint."""
    signup_and_login("alice@example.com")
    asyncio.run(_setup_whatsapp_user("alice@example.com"))

    adapter = _make_adapter()
    asyncio.run(
        handle_whatsapp_message(
            "AC_TEST_123", "whatsapp:+1234567890", "hello", "John",
            adapter=adapter, session_factory=get_session_factory(),
        )
    )
    adapter.send_message.assert_awaited_once()
    text = adapter.send_message.await_args[1]["body"].lower()
    assert "pipeline" in text


def test_known_user_with_agent_dispatches_run(
    client, signup_and_login, auth_header, sample_agent_config, monkeypatch
):
    """Routes to start_run + sends reply via adapter."""
    token = signup_and_login("alice@example.com")
    asyncio.run(_setup_whatsapp_user("alice@example.com"))
    _create_and_deploy(client, token, auth_header, sample_agent_config)

    captured: dict = {}

    async def fake_start_run(session, *, chat_id, user_text):
        captured["chat_id"] = chat_id
        captured["text"] = user_text
        return uuid4()

    async def fake_wait(run_id, *, timeout=60.0):
        return ("succeeded", "echoed: hello")

    monkeypatch.setattr("app.integrations.channels.whatsapp_adapter.start_run", fake_start_run)
    monkeypatch.setattr("app.integrations.channels.slack_adapter.wait_for_reply", fake_wait)

    adapter = _make_adapter()
    asyncio.run(
        handle_whatsapp_message(
            "AC_TEST_123", "whatsapp:+1234567890", "hello", "John",
            adapter=adapter, session_factory=get_session_factory(),
        )
    )

    assert captured["text"] == "hello"
    adapter.send_message.assert_awaited_once()
    assert adapter.send_message.await_args[1]["body"] == "echoed: hello"


def test_same_phone_reuses_chat(
    client, signup_and_login, auth_header, sample_agent_config, monkeypatch
):
    """Two messages from the same phone → same ChatDB row."""
    token = signup_and_login("alice@example.com")
    asyncio.run(_setup_whatsapp_user("alice@example.com"))
    _create_and_deploy(client, token, auth_header, sample_agent_config)

    chat_ids: list = []

    async def fake_start_run(session, *, chat_id, user_text):
        chat_ids.append(chat_id)
        return uuid4()

    async def fake_wait(run_id, *, timeout=60.0):
        return ("succeeded", "ok")

    monkeypatch.setattr("app.integrations.channels.whatsapp_adapter.start_run", fake_start_run)
    monkeypatch.setattr("app.integrations.channels.slack_adapter.wait_for_reply", fake_wait)

    adapter = _make_adapter()
    asyncio.run(
        handle_whatsapp_message(
            "AC_TEST_123", "whatsapp:+1234567890", "m1", "John",
            adapter=adapter, session_factory=get_session_factory(),
        )
    )
    asyncio.run(
        handle_whatsapp_message(
            "AC_TEST_123", "whatsapp:+1234567890", "m2", "John",
            adapter=adapter, session_factory=get_session_factory(),
        )
    )

    assert len(chat_ids) == 2
    assert chat_ids[0] == chat_ids[1]


def test_different_phone_creates_new_chat(
    client, signup_and_login, auth_header, sample_agent_config, monkeypatch
):
    """Two different phones → two separate ChatDB rows."""
    token = signup_and_login("alice@example.com")
    asyncio.run(_setup_whatsapp_user("alice@example.com"))
    _create_and_deploy(client, token, auth_header, sample_agent_config)

    chat_ids: list = []

    async def fake_start_run(session, *, chat_id, user_text):
        chat_ids.append(chat_id)
        return uuid4()

    async def fake_wait(run_id, *, timeout=60.0):
        return ("succeeded", "ok")

    monkeypatch.setattr("app.integrations.channels.whatsapp_adapter.start_run", fake_start_run)
    monkeypatch.setattr("app.integrations.channels.slack_adapter.wait_for_reply", fake_wait)

    adapter = _make_adapter()
    asyncio.run(
        handle_whatsapp_message(
            "AC_TEST_123", "whatsapp:+1111111111", "m1", "Alice",
            adapter=adapter, session_factory=get_session_factory(),
        )
    )
    asyncio.run(
        handle_whatsapp_message(
            "AC_TEST_123", "whatsapp:+2222222222", "m2", "Bob",
            adapter=adapter, session_factory=get_session_factory(),
        )
    )

    assert len(chat_ids) == 2
    assert chat_ids[0] != chat_ids[1]


# ---------------------------------------------------------------------------
# Tests: webhook endpoint (HTTP-level)
# ---------------------------------------------------------------------------


def test_webhook_unknown_account_returns_empty_twiml(client):
    """Unknown AccountSid → 200 with empty TwiML (not an error — Twilio expects 200)."""
    resp = client.post(
        "/whatsapp/webhook",
        data={
            "AccountSid": "AC_NONEXISTENT",
            "From": "whatsapp:+1234567890",
            "Body": "hello",
            "MessageSid": "SM_001",
            "ProfileName": "Test",
            "NumMedia": "0",
        },
    )
    assert resp.status_code == 200
    assert "<Response>" in resp.text


async def test_dedup_fallback_when_redis_down(monkeypatch):
    """Redis unreachable → in-process fallback still enforces the dedup contract."""
    import app.api.whatsapp as wa
    wa._seen_message_sids.clear()

    def _boom():
        raise ConnectionError("redis down")
    monkeypatch.setattr(wa, "get_redis", _boom)

    assert await wa._dedup_check("SM_FIRST") is False   # new
    assert await wa._dedup_check("SM_FIRST") is True    # duplicate
    assert await wa._dedup_check("SM_SECOND") is False  # different sid
    wa._seen_message_sids.clear()


async def test_dedup_cross_replica_via_redis():
    """Redis SET NX dedups across processes; skipped if no reachable Redis."""
    import pytest
    import app.api.whatsapp as wa
    from app.redis_client import get_redis
    try:
        await get_redis().ping()
    except Exception:
        pytest.skip("no reachable Redis")

    sid = f"SM_{__import__('uuid').uuid4().hex}"
    await get_redis().delete(f"wa:seen:{sid}")
    assert await wa._dedup_check(sid) is False  # first time
    assert await wa._dedup_check(sid) is True   # redis remembers it
