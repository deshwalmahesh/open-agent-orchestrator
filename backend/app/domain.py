"""All domain schemas + typed errors in one file.

Pydantic for anything that crosses a serialization boundary (DB rows, HTTP
bodies, SSE events). Tool types live in LangChain (BaseTool); no custom
ToolSpec / ToolContext / ToolResult here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class User(_Base):
    id: UUID = Field(default_factory=uuid4)
    name: str
    slack_user_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Persona(_Base):
    """Named system_prompt. user_id=None = global (read-only); otherwise owned by that user."""

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID | None = None
    name: str
    system_prompt: str


class Chat(_Base):
    """Conversational thread. persona_id, when set, overrides the agent's system_prompt at run time."""

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    agent_id: UUID
    persona_id: UUID | None = None
    channel: Literal["web", "slack"] = "web"
    external_thread_id: str | None = None
    title: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class LLMConfig(_Base):
    """OpenAI-compatible — vLLM, OpenAI, Gemini-via-proxy all fit."""

    base_url: str
    api_key: str = "EMPTY"
    model: str
    # Default tuned for reasoning models (Qwen3.5, DeepSeek-R1, etc.) which need
    # ≥0.6 to avoid degenerate output. Override per-agent for non-reasoning models.
    temperature: float = 0.7
    max_tokens: int = 1024
    timeout_s: float = 30.0


class MemoryConfig(_Base):
    """Rolling summary memory.

    type="summary" (default): keep `window` (N) most-recent messages verbatim; once
    unsummarized count exceeds N + `summary_threshold` (M), fold the oldest M into a
    rolling summary stored on ChatDB. N < M by design: verbatim is expensive per
    message; batched summarization minimises LLM round-trips.
    type="buffer": last-N only, no summary.
    type="none": pass all history (debugging / short chats).
    """

    type: Literal["none", "buffer", "summary"] = "summary"
    window: int = 10  # N — verbatim tail
    summary_threshold: int = 20  # M — unsummarized batch size before fold


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
    tools: list[str] = Field(default_factory=list)  # tool names from app.runtime.tools.REGISTRY
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
