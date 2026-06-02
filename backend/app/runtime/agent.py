"""build_agent(AgentConfig) → CompiledStateGraph via langchain.agents.create_agent.
Retry on transient LLM errors deferred to middleware (P6): `Runnable.with_retry()`
breaks bind_tools, can't be the model arg."""

from __future__ import annotations

import structlog
from langchain.agents import create_agent
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.domain import AgentConfig
from app.llm import build_chat_model
from app.runtime.tools import get_tools

log = structlog.get_logger()


def build_agent(
    cfg: AgentConfig,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    tool_registry: dict[str, BaseTool] | None = None,
) -> CompiledStateGraph:
    """Compile a ReAct agent from an AgentConfig.

    `checkpointer` enables thread persistence (typically the app-level
    AsyncRedisSaver). `tool_registry` is the BYOK seam — pass a per-user
    registry to use that user's credentials for any credentialed tools.
    """
    model = build_chat_model(cfg.llm)
    tools = get_tools(cfg.tools, registry=tool_registry)
    # cfg.id is the AgentConfig pydantic instance UUID (regenerated on each
    # model_validate), NOT the AgentDB row PK. Log cfg.name instead — honest
    # and grep-friendly. The row PK lives on ChatDB.agent_id at the run-service layer.
    log.info(
        "agent.compile",
        agent_name=cfg.name,
        model=cfg.llm.model,
        tools=[t.name for t in tools],
    )
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=cfg.system_prompt,
        checkpointer=checkpointer,
    )
