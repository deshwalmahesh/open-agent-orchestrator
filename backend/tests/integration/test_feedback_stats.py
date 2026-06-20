"""Phase 5d: thumbs up/down feedback + per-user usage stats (runs, reviews, top tools)."""
from __future__ import annotations

import uuid

import pytest

from app.db import create_all, get_session_factory
from app.db.models import ChatDB, FeedbackDB, RunDB, UserDB
from app.db.repos import get_user_stats, upsert_feedback
from app.runtime.usage_callback import UsageCounter


# --- UsageCounter (the standardized capture point) ---

def test_usage_counter_counts_tools():
    c = UsageCounter()
    c.on_tool_start({"name": "web_search"}, "q")
    c.on_tool_start({"name": "web_search"}, "q2")
    c.on_tool_start({}, "x", name="ResearchBot")   # name via kwargs (newer langchain)
    c.on_tool_start({}, "y")                         # neither → "unknown"
    assert c.tool_calls == {"web_search": 2, "ResearchBot": 1, "unknown": 1}


# --- Langfuse observability: disabled by default (no creds), all no-ops ---

def test_langfuse_disabled_is_noop():
    import uuid

    from app import observability as obs
    assert obs.enabled() is False              # no LANGFUSE_* in tests
    assert obs.get_handler() is None           # no callback handler added
    # deterministic trace id (so feedback scores attach to the right trace)
    rid = uuid.uuid4()
    assert obs.trace_id_for(rid) == obs.trace_id_for(rid)
    obs.record_score(rid, name="user-thumbs", value=1)  # must not raise when disabled
    with obs.run_span(rid):                              # nullcontext when disabled
        pass


# --- DB-level: feedback upsert + stats aggregation ---

async def _seed_user_chat():
    sf = get_session_factory()
    async with sf() as s:
        user = UserDB(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@b.c",
                      hashed_password="x", is_active=True)
        s.add(user)
        await s.flush()
        chat = ChatDB(user_id=user.id, agent_id=None)
        s.add(chat)
        await s.commit()
        return user.id, chat.id


@pytest.mark.asyncio
async def test_feedback_upsert_one_per_run():
    await create_all()
    uid, chat_id = await _seed_user_chat()
    sf = get_session_factory()
    async with sf() as s:
        run = RunDB(chat_id=chat_id, status="succeeded")
        s.add(run)
        await s.commit()
        run_id = run.id
    async with sf() as s:
        await upsert_feedback(s, user_id=uid, run_id=run_id, rating="up", comment="nice")
    async with sf() as s:  # re-submit flips up→down, no second row
        await upsert_feedback(s, user_id=uid, run_id=run_id, rating="down", comment=None)
    async with sf() as s:
        rows = (await s.execute(
            FeedbackDB.__table__.select().where(FeedbackDB.user_id == uid)
        )).all()
        assert len(rows) == 1
        stats = await get_user_stats(s, user_id=uid)
    assert stats["reviews_given"] == 1
    assert stats["thumbs_down"] == 1 and stats["thumbs_up"] == 0


@pytest.mark.asyncio
async def test_stats_counts_runs_and_aggregates_tools():
    await create_all()
    uid, chat_id = await _seed_user_chat()
    sf = get_session_factory()
    async with sf() as s:
        s.add_all([
            RunDB(chat_id=chat_id, status="succeeded", tool_calls={"web_search": 2, "calc": 1}),
            RunDB(chat_id=chat_id, status="failed", tool_calls={"web_search": 1}),
        ])
        await s.commit()
    async with sf() as s:
        stats = await get_user_stats(s, user_id=uid)
    assert stats["questions_asked"] == 2
    assert stats["top_tools"] == {"web_search": 3, "calc": 1}  # summed, sorted desc
    assert stats["reviews_given"] == 0


# --- HTTP boundary: feedback endpoint + ownership ---

@pytest.fixture
def _fast_run(monkeypatch):
    """Stub the LLM so the inline run finishes instantly (no retry/backoff against the
    fake endpoint, no slow drain on shutdown). Feedback only needs a run row to exist."""
    from langchain_core.messages import AIMessage, HumanMessage

    async def _stub(agent, messages, config, *, breaker_key=None):
        return {"messages": [HumanMessage(content="hi"), AIMessage(content="ok")]}
    monkeypatch.setattr("app.services.run_service.invoke_with_breaker", _stub)


def _deployed_chat(client, token, auth_header, sample_agent_config) -> tuple[str, str]:
    r = client.post("/agents", json=sample_agent_config("FB"), headers=auth_header(token))
    agent_id = r.json()["id"]
    client.post(f"/agents/{agent_id}/deploy", headers=auth_header(token))
    r = client.post("/chats", json={"agent_id": agent_id}, headers=auth_header(token))
    return r.json()["id"], agent_id


def test_feedback_http_flow_and_stats(client, signup_and_login, auth_header, sample_agent_config, _fast_run):
    token = signup_and_login()
    chat_id, _ = _deployed_chat(client, token, auth_header, sample_agent_config)
    run_id = client.post(f"/chats/{chat_id}/messages", json={"text": "hi"},
                         headers=auth_header(token)).json()["run_id"]

    r = client.post(f"/runs/{run_id}/feedback", json={"rating": "up", "comment": "good"},
                    headers=auth_header(token))
    assert r.status_code == 201, r.text

    stats = client.get("/stats", headers=auth_header(token)).json()
    assert stats["questions_asked"] >= 1
    assert stats["thumbs_up"] == 1 and stats["reviews_given"] == 1


def test_feedback_404_on_other_users_run(client, signup_and_login, auth_header, sample_agent_config, _fast_run):
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    chat_id, _ = _deployed_chat(client, alice, auth_header, sample_agent_config)
    run_id = client.post(f"/chats/{chat_id}/messages", json={"text": "hi"},
                         headers=auth_header(alice)).json()["run_id"]
    # Bob cannot leave feedback on Alice's run.
    r = client.post(f"/runs/{run_id}/feedback", json={"rating": "up"}, headers=auth_header(bob))
    assert r.status_code == 404
