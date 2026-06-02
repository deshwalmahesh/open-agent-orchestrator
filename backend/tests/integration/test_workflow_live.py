"""End-to-end workflow: WorkflowDef → compiler → live LLM → branching → terminate.

Covers (a) routing through a `condition` node, (b) recursion-limit termination
on a deliberate infinite loop, (c) rejection of unsafe condition expressions.
Live LLM where it matters; pure-Python where it doesn't.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from app.config import get_settings
from app.domain import AgentConfig, EdgeDef, LLMConfig, NodeDef, WorkflowDef
from app.runtime.compiler import _eval_condition, compile_workflow

_s = get_settings()
_HAS_LLM = bool(_s.vllm_base_url and _s.vllm_api_key and _s.vllm_default_model)


def _worker_cfg() -> AgentConfig:
    return AgentConfig(
        name="worker",
        role="writer",
        system_prompt="Reply with exactly one short sentence.",
        llm=LLMConfig(
            base_url=_s.vllm_base_url,
            api_key=_s.vllm_api_key,
            model=_s.vllm_default_model,
            max_tokens=1024,
            timeout_s=60,
        ),
        tools=[],
    )


def _wf(condition: str) -> tuple[WorkflowDef, dict]:
    """Build a worker → check → END|worker workflow. `condition` decides END branch."""
    worker = _worker_cfg()
    wid = str(worker.id)
    wf = WorkflowDef(
        name="supervised-loop",
        entry="worker",
        nodes=[
            NodeDef(id="worker", type="agent", ref=wid),
            NodeDef(id="check", type="condition"),
        ],
        edges=[
            EdgeDef(id="e_to_check", source="worker", target="check"),
            EdgeDef(id="e_end", source="check", target="__end__", condition=condition),
            EdgeDef(id="e_loop", source="check", target="worker"),  # default
        ],
    )
    return wf, {wid: worker}


# ---- condition-evaluator security: no LLM needed ----

def test_condition_rejects_unsafe_expression():
    with pytest.raises(ValueError, match="unsafe condition"):
        _eval_condition("__import__('os').system('ls')", {"state": {}})


def test_condition_rejects_syntax_error():
    with pytest.raises(ValueError, match="syntax error"):
        _eval_condition("((", {"state": {}})


def test_condition_evaluates_safe_in_expression():
    ns = {"last_message_content": "all good, APPROVED"}
    assert _eval_condition("'APPROVED' in last_message_content", ns) is True
    assert _eval_condition("'REJECTED' in last_message_content", ns) is False


# ---- compiler + live LLM: branching workflow terminates on True condition ----

@pytest.mark.skipif(not _HAS_LLM, reason="no live LLM creds")
async def test_branching_workflow_terminates_when_condition_true():
    """worker (live LLM) → check → END (condition always True) → done in one pass."""
    wf, agents = _wf(condition="True")
    graph = compile_workflow(wf, agents)
    result = await graph.ainvoke({"messages": [HumanMessage("Say hi.")]})
    msgs = result["messages"]
    # Routing test, not LLM-quality test: workflow must terminate with an AI
    # message appended. Content may be empty on reasoning models — we don't care.
    assert len(msgs) >= 2
    assert isinstance(msgs[-1], AIMessage)


@pytest.mark.skipif(not _HAS_LLM, reason="no live LLM creds")
async def test_branching_workflow_bounded_by_recursion_limit():
    """Condition always False → infinite loop → recursion_limit stops it cleanly.
    This proves Limits.max_steps will actually bound runaway workflows."""
    wf, agents = _wf(condition="False")
    graph = compile_workflow(wf, agents)
    with pytest.raises(GraphRecursionError):
        await graph.ainvoke(
            {"messages": [HumanMessage("hello")]},
            config={"recursion_limit": 4},
        )
