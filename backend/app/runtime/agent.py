"""build_agent_tree(AgentConfig, session) → CompiledStateGraph.

Recursive: sub-agents listed in cfg.subagents are wrapped as LangChain tools
so the parent LLM can delegate via tool-call. Each sub-agent runs its own
ReAct loop (own recursion_limit). Depth is capped at MAX_AGENT_DEPTH.

Retry on transient LLM errors deferred to middleware (P6): `Runnable.with_retry()`
breaks bind_tools, can't be the model arg."""

from __future__ import annotations

import structlog
from langchain.agents import create_agent
from langchain.tools import tool as tool_decorator
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentDB, MCPServerDB
from app.domain import AgentConfig
from app.llm import build_chat_model, invoke_with_retry
from app.runtime.tools import get_tools

log = structlog.get_logger()

MAX_AGENT_DEPTH = 4


def _make_subagent_tool(
    sub_cfg: AgentConfig,
    depth: int,
    session: AsyncSession,
    tool_registry: dict[str, BaseTool] | None = None,
) -> BaseTool:
    """Wrap a sub-agent as a LangChain tool. The parent's LLM calls it by name
    with a single `task` string; the sub-agent runs its own ReAct loop."""

    description = f"{sub_cfg.role}: {sub_cfg.description or sub_cfg.system_prompt[:120]}"

    @tool_decorator(sub_cfg.name, description=description)
    async def _delegate(task: str) -> str:
        sub_agent = await build_agent_tree(
            sub_cfg, depth=depth + 1, session=session, tool_registry=tool_registry,
        )
        result = await invoke_with_retry(
            sub_agent,
            {"messages": [HumanMessage(content=task)]},
            config={"recursion_limit": max(2, sub_cfg.limits.max_steps)},
        )
        return getattr(result["messages"][-1], "content", "") or ""

    return _delegate


async def build_agent_tree(
    cfg: AgentConfig,
    *,
    depth: int = 0,
    session: AsyncSession,
    checkpointer: BaseCheckpointSaver | None = None,
    tool_registry: dict[str, BaseTool] | None = None,
) -> CompiledStateGraph:
    """Compile a ReAct agent tree from an AgentConfig.

    Sub-agents are recursively resolved from DB and wrapped as tools.
    `checkpointer` is only applied to the root agent (depth==0).
    """
    if depth >= MAX_AGENT_DEPTH:
        raise ValueError(f"agent nesting depth {depth} exceeds cap {MAX_AGENT_DEPTH}")

    base_tools = get_tools(cfg.tools, registry=tool_registry)

    # MCP tools — connect to external MCP servers and discover tools at runtime
    mcp_tools: list[BaseTool] = []
    if cfg.mcp_servers:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        server_configs: dict = {}
        for sid in cfg.mcp_servers:
            row = await session.get(MCPServerDB, sid)
            if row is None:
                log.warning("mcp_server.missing", agent=cfg.name, missing_id=str(sid))
                continue
            server_configs[row.name] = {
                "url": row.url, "transport": row.transport, "headers": row.headers or {},
            }
        if server_configs:
            try:
                client = MultiServerMCPClient(server_configs)
                mcp_tools = await client.get_tools()
                log.info("mcp.tools_loaded", agent=cfg.name, tools=[t.name for t in mcp_tools])
            except Exception as exc:
                log.warning("mcp.connect_failed", agent=cfg.name, error=str(exc))

    sub_tools: list[BaseTool] = []
    for sa_id in cfg.subagents:
        sa_row = await session.get(AgentDB, sa_id)
        if sa_row is None:
            log.warning("subagent.missing", parent=cfg.name, missing_id=str(sa_id))
            continue
        sa_cfg = AgentConfig.model_validate(sa_row.config)
        sub_tools.append(_make_subagent_tool(sa_cfg, depth, session, tool_registry))

    all_tools = base_tools + mcp_tools + sub_tools

    log.info(
        "agent.compile",
        agent_name=cfg.name,
        depth=depth,
        model=cfg.llm.model,
        tools=[t.name for t in all_tools],
    )
    return create_agent(
        model=build_chat_model(cfg.llm),
        tools=all_tools,
        system_prompt=cfg.system_prompt,
        checkpointer=checkpointer if depth == 0 else None,
    )
