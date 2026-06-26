"""Integration tests for the human-in-the-loop run lifecycle.

Two layers:
  1. The run_service pause→resume lifecycle (awaiting_human status, reply routing, token
     accounting across legs, reconciler/concurrency exclusion) driven by a fake
     tool-calling model + in-memory checkpointer.
  2. The HIL decision semantics (approve runs the tool, reject skips it) against a
     create_agent graph built from the config build_middleware produces.
  3. The resume endpoint contract (auth / 404 / 409 / 202) via the real TestClient.
"""

import asyncio

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import select

import app.runtime.agent as agent_mod
import app.services.run_service as rs
from app.db import create_all, get_session_factory
from app.db.models import AgentDB, ChatDB, RunDB, UserDB
from app.db import repos
from app.domain import AgentConfig, LLMConfig, utcnow


class _ScriptedModel(BaseChatModel):
    """Returns queued AIMessages one per model node call; bind_tools is a no-op."""

    responses: list = []
    idx: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        msg = self.responses[min(self.idx, len(self.responses) - 1)]
        self.idx += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _ai_tool_call(name, args, usage):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"{name}-1", "type": "tool_call"}],
        usage_metadata=usage,
    )


async def _seed(ask_human=False, hil_tools=None, tools=None):
    """Create a user/agent/chat/run row set on the isolated test DB; return ids."""
    await create_all()
    sf = get_session_factory()
    async with sf() as s:
        u = UserDB(email="hil@test.io", hashed_password="h", plan="free")
        s.add(u)
        await s.commit()
        await s.refresh(u)
        cfg = AgentConfig(
            name="Assistant", role="assistant", system_prompt="help",
            llm=LLMConfig(model="gpt-4o-mini"), tools=tools or [],
            ask_human_enabled=ask_human, hil_tools=hil_tools or [],
        )
        ag = AgentDB(name="Assistant", user_id=u.id, config=cfg.model_dump(mode="json"))
        s.add(ag)
        await s.commit()
        await s.refresh(ag)
        chat = ChatDB(user_id=u.id, agent_id=ag.id, title="t")
        s.add(chat)
        await s.commit()
        await s.refresh(chat)
        run = await repos.create_run(s, chat_id=chat.id, agent_id=ag.id)
        return u.id, chat.id, run.id


# ---- run_service pause/resume lifecycle ---------------------------------

async def test_ask_human_pauses_then_resumes(monkeypatch):
    model = _ScriptedModel(responses=[
        _ai_tool_call("ask_human", {"question": "Approve sending the email?"},
                      {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}),
        AIMessage(content="Email sent.",
                  usage_metadata={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12}),
    ])
    monkeypatch.setattr(agent_mod, "build_chat_model", lambda llm_cfg: model)
    monkeypatch.setattr(rs, "_CHECKPOINTER", InMemorySaver())
    _, chat_id, run_id = await _seed(ask_human=True)

    # First leg pauses for the human.
    await rs._execute(run_id, chat_id, "Please email the client.")
    sf = get_session_factory()
    async with sf() as s:
        row = await s.get(RunDB, run_id)
        assert row.status == "awaiting_human"
        assert row.interrupt["action_requests"][0]["name"] == "ask_human"
        # no agent reply yet
        msgs = await repos.list_messages(s, chat_id=chat_id)
        assert all(m.sender == "user" for m in msgs)

    # Resume with a human answer.
    await rs.resume_run(run_id, [{"type": "respond", "message": "Yes, go ahead."}])
    async with sf() as s:
        row = await s.get(RunDB, run_id)
        assert row.status == "succeeded"
        assert row.interrupt is None
        # root tokens are cumulative in the checkpoint: 15 + 12 = 27 (not double-counted)
        assert row.total_tokens["total_tokens"] == 27
        replies = [m.content for m in await repos.list_messages(s, chat_id=chat_id) if m.sender != "user"]
        assert replies[-1] == "Email sent."


async def test_multi_cycle_pause_resume(monkeypatch):
    """A run can pause for the human more than once. Each resume continues the event
    sequence (no (run_id, seq) collision) and tokens accrue across all legs."""
    model = _ScriptedModel(responses=[
        _ai_tool_call("ask_human", {"question": "Q1?"}, {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}),
        _ai_tool_call("ask_human", {"question": "Q2?"}, {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}),
        AIMessage(content="All set.", usage_metadata={"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}),
    ])
    monkeypatch.setattr(agent_mod, "build_chat_model", lambda llm_cfg: model)
    monkeypatch.setattr(rs, "_CHECKPOINTER", InMemorySaver())
    _, chat_id, run_id = await _seed(ask_human=True)
    sf = get_session_factory()

    await rs._execute(run_id, chat_id, "start")
    async with sf() as s:
        assert (await s.get(RunDB, run_id)).status == "awaiting_human"

    await rs.resume_run(run_id, [{"type": "respond", "message": "A1"}])
    async with sf() as s:
        assert (await s.get(RunDB, run_id)).status == "awaiting_human"  # paused again

    await rs.resume_run(run_id, [{"type": "respond", "message": "A2"}])
    async with sf() as s:
        row = await s.get(RunDB, run_id)
        assert row.status == "succeeded"
        assert row.total_tokens["total_tokens"] == 30  # 10+10+10, cumulative, not doubled
        replies = [m.content for m in await repos.list_messages(s, chat_id=chat_id) if m.sender != "user"]
        assert replies[-1] == "All set."


async def test_run_completes_without_checkpointer(monkeypatch):
    """Regression: no-Redis degraded mode has no checkpointer. _drive must NOT call
    aget_state (it raises 'No checkpointer set') — the run should finish normally."""
    model = _ScriptedModel(responses=[
        AIMessage(content="Plain answer.",
                  usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}),
    ])
    monkeypatch.setattr(agent_mod, "build_chat_model", lambda llm_cfg: model)
    monkeypatch.setattr(rs, "_CHECKPOINTER", None)  # degraded mode
    _, chat_id, run_id = await _seed()
    await rs._execute(run_id, chat_id, "hello")
    sf = get_session_factory()
    async with sf() as s:
        row = await s.get(RunDB, run_id)
        assert row.status == "succeeded", f"expected succeeded, got {row.status} ({row.error})"
        replies = [m.content for m in await repos.list_messages(s, chat_id=chat_id) if m.sender != "user"]
        assert replies[-1] == "Plain answer."


async def test_inbound_message_routes_to_resume(monkeypatch):
    model = _ScriptedModel(responses=[
        _ai_tool_call("ask_human", {"question": "Confirm?"},
                      {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
        AIMessage(content="Done.", usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
    ])
    monkeypatch.setattr(agent_mod, "build_chat_model", lambda llm_cfg: model)
    monkeypatch.setattr(rs, "_CHECKPOINTER", InMemorySaver())
    _, chat_id, run_id = await _seed(ask_human=True)
    await rs._execute(run_id, chat_id, "do it")

    sf = get_session_factory()
    # A new inbound message on a chat with a paused run resumes THAT run (no new run row).
    async with sf() as s:
        returned = await rs.start_run(s, chat_id=chat_id, user_text="yes")
    assert returned == run_id
    await rs.drain_pending(timeout=10)

    async with sf() as s:
        row = await s.get(RunDB, run_id)
        assert row.status == "succeeded"
        all_runs = (await s.execute(select(RunDB).where(RunDB.chat_id == chat_id))).scalars().all()
        assert len(all_runs) == 1  # routed to resume, did not create a second run


async def test_hil_without_checkpointer_fails_fast(monkeypatch):
    """Production-grade: a HIL agent with no checkpointer can't pause/resume, so the run
    must fail loudly at build time — never silently run and blow up mid-flight."""
    model = _ScriptedModel(responses=[AIMessage(content="x")])
    monkeypatch.setattr(agent_mod, "build_chat_model", lambda llm_cfg: model)
    monkeypatch.setattr(rs, "_CHECKPOINTER", None)
    _, chat_id, run_id = await _seed(ask_human=True)
    await rs._execute(run_id, chat_id, "do it")
    sf = get_session_factory()
    async with sf() as s:
        row = await s.get(RunDB, run_id)
        assert row.status == "failed"
        assert row.status != "awaiting_human"  # never pretends to pause


async def test_concurrent_resume_only_one_wins():
    """The conditional UPDATE guards against two resumes driving the same checkpoint."""
    _, _, run_id = await _seed()
    sf = get_session_factory()
    async with sf() as s:
        await repos.pause_run_for_human(s, run_id=run_id, interrupt={"action_requests": []}, partial_tokens={})

    async def attempt():
        async with sf() as s:
            return await repos.mark_run_resumed(s, run_id=run_id)

    a, b = await asyncio.gather(attempt(), attempt())
    won = [r for r in (a, b) if r is not None]
    assert len(won) == 1, "exactly one resume should win the awaiting_human -> running transition"


async def test_reconciler_does_not_reap_awaiting_human():
    """awaiting_human is intentionally long-lived; the orphan reaper must skip it."""
    _, chat_id, run_id = await _seed()
    sf = get_session_factory()
    async with sf() as s:
        await repos.pause_run_for_human(s, run_id=run_id, interrupt={"action_requests": []}, partial_tokens={})
        # backdate started_at far past the stale window
        row = await s.get(RunDB, run_id)
        from datetime import timedelta
        row.started_at = utcnow() - timedelta(hours=72)
        await s.commit()
    reaped = await rs.reconcile_orphaned_runs(stale_after_s=1.0)
    async with sf() as s:
        assert (await s.get(RunDB, run_id)).status == "awaiting_human"
    assert reaped == 0


async def test_concurrency_excludes_awaiting_human():
    user_id, _, run_id = await _seed()
    sf = get_session_factory()
    async with sf() as s:
        await repos.pause_run_for_human(s, run_id=run_id, interrupt={"action_requests": []}, partial_tokens={})
        # a paused run is not "active" — it occupies no worker
        assert await repos.count_active_runs(s, user_id=user_id) == 0


# ---- HIL decision semantics (approve runs the tool, reject skips it) -----

@tool
def adder(a: int, b: int) -> int:
    """add two numbers"""
    return a + b


def _hil_agent():
    model = _ScriptedModel(responses=[
        _ai_tool_call("adder", {"a": 2, "b": 3}, {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
        AIMessage(content="The sum is 5."),
    ])
    return create_agent(
        model=model, tools=[adder],
        middleware=[HumanInTheLoopMiddleware(interrupt_on={"adder": True})],
        checkpointer=InMemorySaver(),
    )


async def test_strict_approve_runs_the_tool():
    agent = _hil_agent()
    cfg = {"configurable": {"thread_id": "t-approve"}}
    await agent.ainvoke({"messages": [HumanMessage(content="add 2 and 3")]}, config=cfg)
    assert (await agent.aget_state(cfg)).next  # paused
    await agent.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config=cfg)
    state = await agent.aget_state(cfg)
    assert not state.next
    tool_msgs = [m for m in state.values["messages"] if getattr(m, "name", None) == "adder"]
    assert tool_msgs and tool_msgs[-1].content == "5"  # the tool actually executed


async def test_strict_reject_skips_the_tool():
    agent = _hil_agent()
    cfg = {"configurable": {"thread_id": "t-reject"}}
    await agent.ainvoke({"messages": [HumanMessage(content="add 2 and 3")]}, config=cfg)
    await agent.ainvoke(
        Command(resume={"decisions": [{"type": "reject", "message": "don't add"}]}), config=cfg
    )
    state = await agent.aget_state(cfg)
    tool_msgs = [m for m in state.values["messages"] if getattr(m, "name", None) == "adder"]
    # the tool was NOT executed: its ToolMessage carries the rejection, not the result "5"
    assert tool_msgs and tool_msgs[-1].content != "5"
    assert tool_msgs[-1].status == "error"


# ---- resume endpoint contract -------------------------------------------

def test_resume_endpoint_validation(client, signup_and_login, auth_header):
    nil = "00000000-0000-0000-0000-000000000000"
    # missing auth -> 401
    assert client.post(f"/runs/{nil}/resume", json={"decisions": [{"type": "approve"}]}).status_code == 401
    token = signup_and_login()
    h = auth_header(token)
    # unknown run -> 404
    assert client.post(f"/runs/{nil}/resume", json={"decisions": [{"type": "approve"}]}, headers=h).status_code == 404

    async def _seed_run(awaiting):
        sf = get_session_factory()
        async with sf() as s:
            u = (await s.execute(select(UserDB))).scalars().first()
            chat = ChatDB(user_id=u.id, agent_id=None, title="t")
            s.add(chat)
            await s.commit()
            await s.refresh(chat)
            run = await repos.create_run(s, chat_id=chat.id, agent_id=None)
            if awaiting:
                await repos.pause_run_for_human(
                    s, run_id=run.id,
                    interrupt={"action_requests": [{"name": "ask_human", "args": {}}]}, partial_tokens={},
                )
            return str(run.id)

    # not awaiting -> 409
    rid = asyncio.run(_seed_run(False))
    assert client.post(f"/runs/{rid}/resume", json={"decisions": [{"type": "approve"}]}, headers=h).status_code == 409
    # awaiting -> 202
    rid2 = asyncio.run(_seed_run(True))
    r = client.post(f"/runs/{rid2}/resume", json={"decisions": [{"type": "respond", "message": "hi"}]}, headers=h)
    assert r.status_code == 202 and r.json()["status"] == "resuming"
