"""All domain schemas + typed errors in one file.

Pydantic for anything that crosses a serialization boundary (DB rows, HTTP
bodies, SSE events). Dataclasses for pure-runtime types (ToolContext,
ToolResult). BaseTool is an ABC.

KISS: only fields that have a caller today, plus the few sub-models that
TASK.md asks for and that map cleanly to UI form sections.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Base + helpers
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# Users, personas, chats
# ---------------------------------------------------------------------------

class User(_Base):
    id: UUID = Field(default_factory=uuid4)
    name: str
    slack_user_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Persona(_Base):
    """Per-(agent, user) system-prompt override. user_id=None = default."""

    id: UUID = Field(default_factory=uuid4)
    agent_id: UUID
    user_id: UUID | None = None
    system_prompt_override: str


class Chat(_Base):
    """Persistent conversational thread. Exactly one of agent_id/workflow_id set."""

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    agent_id: UUID | None = None
    workflow_id: UUID | None = None
    channel: Literal["web", "slack"] = "web"
    external_thread_id: str | None = None
    title: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Agent config — TASK.md's dimensions, nothing more
# ---------------------------------------------------------------------------

class LLMConfig(_Base):
    """OpenAI-compatible — vLLM, OpenAI, Gemini-via-proxy all fit."""

    base_url: str
    api_key: str = "EMPTY"
    model: str
    temperature: float = 0.2
    max_tokens: int = 1024
    timeout_s: float = 30.0


class MemoryConfig(_Base):
    type: Literal["none", "buffer", "summary"] = "buffer"
    window: int = 20


class Limits(_Base):
    max_steps: int = 8
    max_tokens_per_run: int | None = None


class Guardrails(_Base):
    blocked_topics: list[str] = Field(default_factory=list)
    require_human_approval_for: list[str] = Field(default_factory=list)  # tool names


class ChannelBinding(_Base):
    channel: Literal["slack", "web"]
    external_id: str | None = None  # e.g., slack channel id


class AgentConfig(_Base):
    id: UUID = Field(default_factory=uuid4)
    name: str
    role: str
    description: str | None = None
    system_prompt: str
    llm: LLMConfig
    tools: list[str] = Field(default_factory=lambda: ["plan"])  # planner auto-attached, removable
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    limits: Limits = Field(default_factory=Limits)
    guardrails: Guardrails = Field(default_factory=Guardrails)
    # Flattened from prior InteractionRules sub-model:
    can_delegate_to: list[UUID] = Field(default_factory=list)
    expose_as_tool: bool = False
    skills: list[str] = Field(default_factory=list)
    schedules: list[str] = Field(default_factory=list)  # cron strings; stretch (P6)
    channels: list[ChannelBinding] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Tool contract — uniform across plain tools and (future) agent-as-tool
# ---------------------------------------------------------------------------

class ToolSpec(_Base):
    """LLM-visible tool description. JSON-Schema params (OpenAI-compatible)."""

    name: str
    description: str
    parameters: dict[str, Any]
    needs_approval: bool = False
    tags: list[str] = Field(default_factory=list)


@dataclass
class ToolResult:
    output: Any
    success: bool = True
    error: str | None = None


EmitEvent = Callable[["RunEvent"], Awaitable[None]]


@dataclass
class ToolContext:
    """Runtime context handed to every tool. Not serialized."""

    run_id: UUID
    chat_id: UUID
    agent_id: UUID
    user_id: UUID
    emit: EmitEvent
    llm: Any = None     # LLMClient — typed in caller to avoid circular import
    http: Any = None    # httpx.AsyncClient
    extra: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    """All tools — Tavily, calculator, planner, future agent-as-tool — share this."""

    spec: ToolSpec

    @abstractmethod
    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...


# ---------------------------------------------------------------------------
# Workflow graph (React Flow shape — stored verbatim, compiled by runtime)
# ---------------------------------------------------------------------------

NodeType = Literal["start", "agent", "tool", "condition", "human", "end"]


class NodeDef(_Base):
    id: str
    type: NodeType
    ref: str | None = None  # agent UUID (str) or tool name; None for start/end/condition
    config: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] | None = None  # UI passthrough


class EdgeDef(_Base):
    id: str
    source: str
    target: str
    condition: str | None = None  # safe expr; only meaningful on conditional source


class WorkflowDef(_Base):
    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str = ""
    entry: str
    nodes: list[NodeDef]
    edges: list[EdgeDef]
    is_template: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Messages + runs + events
# ---------------------------------------------------------------------------

MessageSender = str  # "user" | "system" | agent UUID string


class Message(_Base):
    """One model for both in-graph accumulator and persisted DB row.

    id/chat_id/run_id are None for in-flight messages; populated when persisted.
    """

    id: UUID | None = None
    chat_id: UUID | None = None
    run_id: UUID | None = None
    sender: MessageSender
    recipient: MessageSender | None = None
    content: str
    ts: datetime = Field(default_factory=utcnow)


RunStatus = Literal["pending", "running", "succeeded", "failed", "interrupted"]


class Run(_Base):
    id: UUID = Field(default_factory=uuid4)
    chat_id: UUID
    agent_id: UUID | None = None
    workflow_id: UUID | None = None
    status: RunStatus = "pending"
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    total_tokens: dict[str, int] = Field(default_factory=dict)  # prompt/completion/total
    total_cost: float = 0.0
    error: str | None = None


EventType = Literal[
    "run.started",
    "node.started",
    "node.ended",
    "llm.call",
    "tool.start",
    "tool.end",
    "agent.message",
    "usage",
    "guardrail.blocked",
    "human.requested",
    "run.error",
    "run.finished",
]


class RunEvent(_Base):
    run_id: UUID
    seq: int
    ts: datetime = Field(default_factory=utcnow)
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------

class AppError(Exception):
    """Base for all domain/runtime errors."""


class NotFoundError(AppError):
    def __init__(self, resource: str, ident: str | UUID) -> None:
        super().__init__(f"{resource} not found: {ident}")
        self.resource = resource
        self.ident = str(ident)


class ToolError(AppError):
    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(f"tool '{tool_name}' failed: {message}")
        self.tool_name = tool_name


class GuardrailViolation(AppError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"guardrail blocked: {reason}")
        self.reason = reason


class BudgetExceeded(AppError):
    def __init__(self, kind: str, limit: int) -> None:
        super().__init__(f"{kind} budget exceeded (limit={limit})")
        self.kind = kind
        self.limit = limit
