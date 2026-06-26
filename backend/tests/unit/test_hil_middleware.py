"""Unit tests for HIL + forced-chain middleware logic (no LLM, no DB).

Covers the parts we OWN: forced-edge injection (require_before_finish / force_after),
arg synthesis, middleware assembly from config, the channel free-text→decision mapping,
and the interrupt→question rendering. The interrupt/resume primitive itself is a
LangGraph feature exercised in the integration tests."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.domain import AgentConfig, ForcedRule, LLMConfig
from app.integrations.channels.slack_adapter import _interrupt_question
from app.runtime.middleware import (
    ForcedChainMiddleware,
    _build_args,
    build_middleware,
)
from app.services.run_service import _decisions_from_text


@tool
def validator(task: str) -> str:
    """validate the draft"""
    return "ok"


@tool
def tool_a(x: str) -> str:
    """a"""
    return "A-out"


@tool
def tool_b(y: str) -> str:
    """b"""
    return "B-out"


@tool
def no_args() -> str:
    """no args"""
    return "z"


TOOLS = {t.name: t for t in (validator, tool_a, tool_b, no_args)}


def _cfg(**overrides) -> AgentConfig:
    base = AgentConfig(name="a", role="r", system_prompt="p", llm=LLMConfig(model="m"))
    return base.model_copy(update=overrides)


# ---- build_middleware assembly ------------------------------------------

def test_no_middleware_when_unconfigured():
    assert build_middleware(_cfg(), list(TOOLS.values())) == []


def test_hil_middleware_built_from_flags():
    cfg = _cfg(ask_human_enabled=True, hil_tools=["tool_a"])
    mw = build_middleware(cfg, list(TOOLS.values()))
    assert len(mw) == 1
    assert set(mw[0].interrupt_on.keys()) == {"ask_human", "tool_a"}


def test_forced_and_hil_both_built():
    cfg = _cfg(
        ask_human_enabled=True,
        forced_rules=[ForcedRule(kind="require_before_finish", target="validator")],
    )
    mw = build_middleware(cfg, list(TOOLS.values()))
    assert len(mw) == 2  # HIL + forced chain


# ---- arg synthesis ------------------------------------------------------

def test_build_args_single_string_arg():
    assert _build_args(validator, "DRAFT") == {"task": "DRAFT"}


def test_build_args_no_args_tool():
    assert _build_args(no_args, "ignored") == {}


# ---- ForcedChainMiddleware ----------------------------------------------

def test_unknown_target_rule_dropped():
    mw = ForcedChainMiddleware(
        [ForcedRule(kind="force_after", target="ghost", then="tool_b")], TOOLS
    )
    assert mw.rules == []


def test_require_before_finish_injects_when_missing():
    mw = ForcedChainMiddleware(
        [ForcedRule(kind="require_before_finish", target="validator")], TOOLS
    )
    final = AIMessage(content="here is my answer")
    out = mw.after_model({"messages": [HumanMessage(content="hi"), final]}, None)
    assert out is not None and out["jump_to"] == "tools"
    injected = out["messages"][0].tool_calls
    assert len(injected) == 1
    assert injected[0]["name"] == "validator"
    assert injected[0]["args"] == {"task": "here is my answer"}


def test_require_before_finish_allows_finish_once_validated():
    mw = ForcedChainMiddleware(
        [ForcedRule(kind="require_before_finish", target="validator")], TOOLS
    )
    msgs = [
        HumanMessage(content="hi"),
        AIMessage(content="", tool_calls=[{"name": "validator", "args": {"task": "d"}, "id": "1", "type": "tool_call"}]),
        ToolMessage(content="ok", name="validator", tool_call_id="1"),
        AIMessage(content="final answer"),
    ]
    assert mw.after_model({"messages": msgs}, None) is None


def test_require_before_finish_ignores_midflight_tool_calls():
    """When the model is itself calling a tool, don't force the validator yet."""
    mw = ForcedChainMiddleware(
        [ForcedRule(kind="require_before_finish", target="validator")], TOOLS
    )
    msgs = [
        HumanMessage(content="hi"),
        AIMessage(content="", tool_calls=[{"name": "tool_a", "args": {"x": "1"}, "id": "a", "type": "tool_call"}]),
    ]
    assert mw.after_model({"messages": msgs}, None) is None


def test_force_after_chains_a_to_b():
    mw = ForcedChainMiddleware(
        [ForcedRule(kind="force_after", target="tool_a", then="tool_b")], TOOLS
    )
    msgs = [
        HumanMessage(content="hi"),
        AIMessage(content="", tool_calls=[{"name": "tool_a", "args": {"x": "1"}, "id": "a1", "type": "tool_call"}]),
        ToolMessage(content="A-out", name="tool_a", tool_call_id="a1"),
        AIMessage(content="done"),
    ]
    out = mw.after_model({"messages": msgs}, None)
    assert out is not None and out["jump_to"] == "tools"
    forced = out["messages"][0].tool_calls[-1]
    assert forced["name"] == "tool_b"
    assert forced["args"] == {"y": "A-out"}  # filled with A's output


def test_force_after_not_retriggered_once_b_ran():
    mw = ForcedChainMiddleware(
        [ForcedRule(kind="force_after", target="tool_a", then="tool_b")], TOOLS
    )
    msgs = [
        HumanMessage(content="hi"),
        AIMessage(content="", tool_calls=[{"name": "tool_a", "args": {"x": "1"}, "id": "a1", "type": "tool_call"}]),
        ToolMessage(content="A-out", name="tool_a", tool_call_id="a1"),
        AIMessage(content="", tool_calls=[{"name": "tool_b", "args": {"y": "A-out"}, "id": "b1", "type": "tool_call"}]),
        ToolMessage(content="B-out", name="tool_b", tool_call_id="b1"),
        AIMessage(content="done"),
    ]
    assert mw.after_model({"messages": msgs}, None) is None


# ---- channel free-text → decision mapping -------------------------------

def test_decisions_ask_human_is_respond():
    interrupt = {"action_requests": [{"name": "ask_human", "args": {"question": "?"}}]}
    assert _decisions_from_text("the answer is 42", interrupt) == [
        {"type": "respond", "message": "the answer is 42"}
    ]


def test_decisions_forced_tool_affirmative_is_approve():
    interrupt = {"action_requests": [{"name": "send_email", "args": {}}]}
    assert _decisions_from_text("yes", interrupt) == [{"type": "approve"}]


def test_decisions_forced_tool_negative_is_reject():
    interrupt = {"action_requests": [{"name": "send_email", "args": {}}]}
    assert _decisions_from_text("no, wrong recipient", interrupt) == [
        {"type": "reject", "message": "no, wrong recipient"}
    ]


def test_decisions_one_per_action():
    interrupt = {"action_requests": [{"name": "ask_human", "args": {}}, {"name": "send_email", "args": {}}]}
    decisions = _decisions_from_text("yes", interrupt)
    assert len(decisions) == 2  # middleware requires exactly one decision per pending action


# ---- interrupt → question rendering -------------------------------------

def test_interrupt_question_prefers_ask_human_question():
    interrupt = {"action_requests": [{"name": "ask_human", "args": {"question": "Approve send?"}}]}
    assert _interrupt_question(interrupt) == "Approve send?"


def test_interrupt_question_falls_back_to_description():
    interrupt = {"action_requests": [{"name": "send_email", "args": {"to": "x"}, "description": "Run send_email?"}]}
    assert _interrupt_question(interrupt) == "Run send_email?"


def test_interrupt_question_empty_default():
    assert _interrupt_question({}) == "I need your input to continue."
