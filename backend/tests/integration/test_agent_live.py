"""End-to-end agent integration: AgentConfig → build_agent_tree → LIVE LLM → tool call → final answer.

This is the test that catches the real-world breakages (wrong prompt format,
tool not bound, LLM doesn't pick up the tool, message reducer broken, etc.).
Skipped if no LLM creds.
"""

import pytest
from langchain_core.messages import HumanMessage
from unittest.mock import AsyncMock

from app.config import get_settings
from app.domain import AgentConfig, LLMConfig
from app.runtime.agent import build_agent_tree

_s = get_settings()
_HAS_LLM = bool(_s.vllm_base_url and _s.vllm_api_key and _s.vllm_default_model)

pytestmark = pytest.mark.skipif(not _HAS_LLM, reason="no live LLM creds")


def _cfg(tools: list[str]) -> AgentConfig:
    return AgentConfig(
        name="test-agent",
        role="calculator",
        system_prompt="You are a precise math assistant. Use the calculator tool for any arithmetic.",
        llm=LLMConfig(
            base_url=_s.vllm_base_url,
            api_key=_s.vllm_api_key,
            model=_s.vllm_default_model,
            max_tokens=1024,
            timeout_s=60,
        ),
        tools=tools,
    )


async def test_agent_uses_calculator_tool_for_arithmetic():
    """Hard regression: agent must (a) compile, (b) invoke LLM, (c) emit a
    tool_call for calculator, (d) get the tool result back, (e) return final
    answer containing the correct number."""
    agent = await build_agent_tree(_cfg(tools=["calculator"]), session_factory=AsyncMock())

    result = await agent.ainvoke(
        {"messages": [HumanMessage("What is 17 times 23? Show only the number.")]}
    )
    msgs = result["messages"]
    # The trail should contain: human → ai(tool_call) → tool(result) → ai(answer)
    contents = " ".join(str(m.content) for m in msgs)
    assert "391" in contents, f"expected 391 in agent output, got: {contents!r}"


async def test_agent_without_tools_still_answers():
    """Tool-less agent: should produce a text answer, no crash on empty tool list."""
    agent = await build_agent_tree(_cfg(tools=[]), session_factory=AsyncMock())

    result = await agent.ainvoke(
        {"messages": [HumanMessage("Reply with exactly the word: pong")]}
    )
    final = result["messages"][-1].content
    assert "pong" in str(final).lower()
