"""WorkflowDef → CompiledStateGraph. Only needed for branching workflows;
single-agent chats use build_agent(cfg).ainvoke directly. Condition expressions
are ast-whitelist sandboxed — no builtins, no calls, no comprehensions."""

from __future__ import annotations

import ast
from collections.abc import Callable

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domain import AgentConfig, EdgeDef, NotFoundError, WorkflowDef
from app.runtime.agent import build_agent
from app.runtime.state import GraphState

log = structlog.get_logger()

# AST nodes allowed in condition expressions. Tight whitelist — only what
# `'X' in state[...]` style routing actually uses. No Call, no Lambda, no
# comprehensions, no Slice (we don't range-index in conditions).
_ALLOWED = (
    ast.Expression,
    ast.Compare,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.UAdd, ast.USub,
    ast.Name, ast.Load,
    ast.Attribute, ast.Subscript,
    ast.Constant,
    ast.Eq, ast.NotEq, ast.Lt, ast.Gt, ast.LtE, ast.GtE, ast.In, ast.NotIn,
)


def _validate_condition(expr: str) -> ast.Expression:
    """Parse + AST-walk-validate. Rejects anything outside the whitelist."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"condition syntax error: {expr!r} ({exc})") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(
                f"unsafe condition (disallowed AST node {type(node).__name__}): {expr!r}"
            )
    return tree


def _eval_condition(expr: str, ns: dict) -> bool:
    _validate_condition(expr)
    return bool(eval(expr, {"__builtins__": {}}, ns))  # noqa: S307 — validated above


def _passthrough(state: GraphState) -> dict:
    """Condition node body — routing happens in the outgoing edges."""
    return {}


def _build_router(
    edges: list[EdgeDef], resolve_target: Callable[[str], str]
) -> tuple[Callable[[GraphState], str], dict[str, str]]:
    """Router fn + path map. Evaluates conditions in declaration order; first
    True wins. An edge with `condition=None` is the default fallback."""

    def router(state: GraphState) -> str:
        last_msg = state["messages"][-1] if state.get("messages") else None
        last_content = str(getattr(last_msg, "content", "") or "")
        ns = {
            "state": state,
            "last_message": last_msg,
            "last_message_content": last_content,
        }
        default: str | None = None
        for e in edges:
            if e.condition is None:
                default = e.id
                continue
            if _eval_condition(e.condition, ns):
                return e.id
        if default is None:
            raise ValueError(
                "no conditional edge matched and no default (unconditional) edge present"
            )
        return default

    path_map = {e.id: resolve_target(e.target) for e in edges}
    return router, path_map


def compile_workflow(
    wf: WorkflowDef,
    agents: dict[str, AgentConfig],
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    tool_registry=None,
) -> CompiledStateGraph:
    """Compile a WorkflowDef. `agents` maps agent_id (str UUID) → AgentConfig."""
    virtual = {n.id for n in wf.nodes if n.type in ("start", "end")}

    def resolve_target(node_id: str) -> str:
        return END if node_id in virtual or node_id == "__end__" else node_id

    g = StateGraph(GraphState)
    for n in wf.nodes:
        if n.type == "agent":
            if n.ref is None or n.ref not in agents:
                raise NotFoundError("agent", str(n.ref))
            g.add_node(n.id, build_agent(agents[n.ref], tool_registry=tool_registry))
        elif n.type == "condition":
            g.add_node(n.id, _passthrough)
        elif n.type in ("start", "end"):
            continue
        else:
            # `tool` and `human` defer — `tool` rarely useful (agent already
            # owns its tools); `human` is HITL in P6.
            raise ValueError(f"unsupported node type: {n.type!r}")

    for n in wf.nodes:
        if n.id in virtual:
            continue
        outs = [e for e in wf.edges if e.source == n.id]
        if not outs:
            continue
        if any(e.condition for e in outs):
            router, path_map = _build_router(outs, resolve_target)
            g.add_conditional_edges(n.id, router, path_map)
        else:
            for e in outs:
                g.add_edge(n.id, resolve_target(e.target))

    g.set_entry_point(wf.entry)
    log.info(
        "workflow.compile",
        workflow_id=str(wf.id),
        name=wf.name,
        node_count=len(wf.nodes),
        edge_count=len(wf.edges),
    )
    return g.compile(checkpointer=checkpointer)
