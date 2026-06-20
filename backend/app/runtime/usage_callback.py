"""Standardized per-run usage capture: one callback handler passed into the agent
invoke config — NOT a decorator sprinkled on every tool. LangGraph propagates the
config (and thus this handler) down to every tool/sub-agent node, so a single
integration point counts all calls, including nested ones.

Sub-agents are LangChain tools in this system, so they're counted by their agent name
alongside built-in tools (e.g. {"web_search": 3, "ResearchBot": 1}).
"""
from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler


class UsageCounter(BaseCallbackHandler):
    def __init__(self) -> None:
        self.tool_calls: dict[str, int] = {}

    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        # Tool name lives in `serialized` (older) or kwargs (newer langchain). Be defensive.
        name = (serialized or {}).get("name") or kwargs.get("name") or "unknown"
        self.tool_calls[name] = self.tool_calls.get(name, 0) + 1
