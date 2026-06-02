"""build_agent_tree(AgentConfig, session_factory) → CompiledStateGraph.

Recursive: sub-agents listed in cfg.subagents are wrapped as LangChain tools
so the parent LLM can delegate via tool-call. Each sub-agent runs its own
ReAct loop (own recursion_limit). Depth is capped at MAX_AGENT_DEPTH.

Builds open a short-lived session for DB lookups (MCP server rows, sub-agent
config rows) and close it before returning. The compiled graph holds NO open
DB session — LLM calls and tool execution run without a pinned connection.
Sub-agent delegation captures the factory and opens its own session on invoke.

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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AgentDB, MCPServerDB
from app.domain import AgentConfig
from app.llm import build_chat_model, invoke_with_retry
from app.runtime.tools import get_tools

log = structlog.get_logger()

MAX_AGENT_DEPTH = 4


def _make_subagent_tool(
    sub_cfg: AgentConfig,
    depth: int,
    session_factory: async_sessionmaker[AsyncSession],
    tool_registry: dict[str, BaseTool] | None = None,
) -> BaseTool:
    """Wrap a sub-agent as a LangChain tool. The parent's LLM calls it by name
    with a single `task` string; the sub-agent runs its own ReAct loop. The
    factory is captured so the sub-agent build can open its own session at
    invoke time (no shared session held across the parent's LLM call)."""

    description = f"{sub_cfg.role}: {sub_cfg.description or sub_cfg.system_prompt[:120]}"

    @tool_decorator(sub_cfg.name, description=description)
    async def _delegate(task: str) -> str:
        sub_agent = await build_agent_tree(
            sub_cfg, depth=depth + 1, session_factory=session_factory, tool_registry=tool_registry,
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
    session_factory: async_sessionmaker[AsyncSession],
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

    # Resolve MCP server rows + sub-agent config rows in one short-lived session, then close.
    # Network calls (MCP get_tools) happen AFTER the session closes so we never pin a
    # connection during external I/O.
    server_configs: dict = {}
    sub_cfgs: list[AgentConfig] = []
    if cfg.mcp_servers or cfg.subagents:
        async with session_factory() as session:
            for sid in cfg.mcp_servers:
                row = await session.get(MCPServerDB, sid)
                if row is None:
                    log.warning("mcp_server.missing", agent=cfg.name, missing_id=str(sid))
                    continue
                server_configs[row.name] = {
                    "url": row.url, "transport": row.transport, "headers": row.headers or {},
                }
            for sa_id in cfg.subagents:
                sa_row = await session.get(AgentDB, sa_id)
                if sa_row is None:
                    log.warning("subagent.missing", parent=cfg.name, missing_id=str(sa_id))
                    continue
                sub_cfgs.append(AgentConfig.model_validate(sa_row.config))

    mcp_tools: list[BaseTool] = []
    if server_configs:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        try:
            client = MultiServerMCPClient(server_configs)
            mcp_tools = await client.get_tools()
            log.info("mcp.tools_loaded", agent=cfg.name, tools=[t.name for t in mcp_tools])
        except Exception as exc:
            log.warning("mcp.connect_failed", agent=cfg.name, error=str(exc))

    sub_tools: list[BaseTool] = [
        _make_subagent_tool(sa_cfg, depth, session_factory, tool_registry) for sa_cfg in sub_cfgs
    ]

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
