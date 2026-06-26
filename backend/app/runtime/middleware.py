"""Agent middleware assembly: human-in-the-loop + forced chains.

Both plug into `create_agent(middleware=...)` (langchain 1.x) and are applied only at
the root agent (depth 0) — that's where the Redis checkpointer lives, which interrupts
require to pause/resume.

- HIL is the shipped `HumanInTheLoopMiddleware`: it interrupts before designated tool
  calls and resumes with a human decision (approve/edit/reject/respond). We write no
  interrupt code — only the config that turns it on.
- ForcedChainMiddleware overrides the free ReAct loop with deterministic edges the LLM
  cannot skip, using the `after_model` hook's `jump_to` (langchain types.JumpTo).
"""

from __future__ import annotations

import uuid

import structlog
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    hook_config,
)
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langchain_core.tools import BaseTool

from app.domain import AgentConfig, ForcedRule

log = structlog.get_logger()


def _tool_names_called(messages: list) -> set[str]:
    """Names of every tool the agent has already invoked in this run."""
    names: set[str] = set()
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            names.add(tc["name"])
    return names


def _last_ai(messages: list) -> AIMessage | None:
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            return m
    return None


def _last_tool_result_for(messages: list, name: str) -> str | None:
    """Content of the most recent successful ToolMessage produced by `name`, if any."""
    for m in reversed(messages):
        if isinstance(m, ToolMessage) and m.name == name:
            return m.content if isinstance(m.content, str) else str(m.content)
    return None


def _build_args(tool: BaseTool, fill_text: str) -> dict | None:
    """Best-effort args for a FORCED tool call.

    Sub-agent tools and most validators take a single string input; we fill it with the
    draft answer / upstream output so the forced step has something to act on.
    Returns None when we can't safely construct args (tool needs multiple/none-string
    required args) — caller then skips forcing rather than wedge the run with a call
    that will fail schema validation.
    """
    schema = tool.args  # {arg_name: json-schema-property}
    if not schema:
        return {}
    string_args = [n for n, p in schema.items() if p.get("type", "string") == "string"]
    if string_args:
        # Fill the first string arg; any other args fall back to their defaults. A
        # missing non-defaulted arg will make the tool error and the LLM react.
        return {string_args[0]: fill_text}
    log.warning("forced_chain.unfillable_target", tool=tool.name)
    return None


def _inject_call(ai_msg: AIMessage, tool: BaseTool, fill_text: str) -> bool:
    """Append a forced tool call to `ai_msg`. Returns True if injected."""
    args = _build_args(tool, fill_text)
    if args is None:
        return False
    ai_msg.tool_calls = [
        *(ai_msg.tool_calls or []),
        ToolCall(name=tool.name, args=args, id=f"forced_{uuid.uuid4().hex}", type="tool_call"),
    ]
    return True


class ForcedChainMiddleware(AgentMiddleware):
    """Enforce config-declared edges the LLM cannot skip.

    - require_before_finish: when the model tries to give a final answer but `target`
      has never run, force a call to `target` (filled with the draft answer) and route
      back to the tools node. The model only finishes once `target` has run.
    - force_after: once `target` (tool A) has produced a result, force `then` (tool B)
      before the agent proceeds.

    Termination: once a forced tool is called its name enters `_tool_names_called`, so it
    is never injected twice — the loop is also bounded by the run's recursion_limit.
    """

    def __init__(self, rules: list[ForcedRule], tools_by_name: dict[str, BaseTool]) -> None:
        super().__init__()
        # Drop rules whose target/then tool isn't on this agent — a forced edge to a
        # non-existent tool can never fire and is a config error worth surfacing.
        self.rules: list[ForcedRule] = []
        for r in rules:
            missing = [t for t in (r.target, r.then) if t and t not in tools_by_name]
            if missing:
                log.warning("forced_chain.unknown_tool", kind=r.kind, missing=missing)
                continue
            self.rules.append(r)
        self.tools_by_name = tools_by_name

    @hook_config(can_jump_to=["tools", "model", "end"])
    def after_model(self, state, runtime):  # noqa: ANN001 — AgentState/Runtime from langchain
        messages = state["messages"]
        last = _last_ai(messages)
        if last is None:
            return None
        called = _tool_names_called(messages)

        for r in self.rules:
            if r.kind == "require_before_finish":
                # Only act when the model is trying to FINISH (no pending tool calls).
                if last.tool_calls or r.target in called:
                    continue
                draft = last.content if isinstance(last.content, str) else ""
                if _inject_call(last, self.tools_by_name[r.target], draft):
                    log.info("forced_chain.require_before_finish", target=r.target)
                    return {"messages": [last], "jump_to": "tools"}

            elif r.kind == "force_after":
                # A has produced a result and B has not run yet → force B next.
                if r.target not in called or r.then in called:
                    continue
                upstream = _last_tool_result_for(messages, r.target) or ""
                # Don't clobber tool calls the model is already making this turn.
                if last.tool_calls:
                    continue
                if _inject_call(last, self.tools_by_name[r.then], upstream):
                    log.info("forced_chain.force_after", after=r.target, forced=r.then)
                    return {"messages": [last], "jump_to": "tools"}

        return None


def build_middleware(cfg: AgentConfig, tools: list[BaseTool]) -> list[AgentMiddleware]:
    """Assemble the middleware stack for an agent from its config + resolved tools.

    Returns [] when nothing is configured (the common case) so plain agents are
    unaffected. HIL is listed before the forced chain; their conditions rarely overlap
    (forced targets are usually not HIL tools), so order is not load-bearing here.
    """
    middleware: list[AgentMiddleware] = []

    interrupt_on: dict[str, bool] = {}
    if cfg.ask_human_enabled:
        interrupt_on["ask_human"] = True  # flexible: agent calls it at will
    for name in cfg.hil_tools:
        interrupt_on[name] = True  # strict: force a pause whenever this tool is called
    if interrupt_on:
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))

    if cfg.forced_rules:
        middleware.append(ForcedChainMiddleware(cfg.forced_rules, {t.name: t for t in tools}))

    return middleware
