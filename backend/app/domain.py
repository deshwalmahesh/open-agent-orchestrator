"""Domain schemas: AgentConfig, LLMConfig, MemoryConfig, RunEvent.

ORM models live in db/models.py; these are the Pydantic contracts for
serialization boundaries (HTTP bodies, SSE events, JSON config blobs).
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
    subagents: list[UUID] = Field(default_factory=list)  # agent UUIDs wrapped as tools at runtime
    skills: list[UUID] = Field(default_factory=list)  # SkillDB UUIDs — content injected into prompt at runtime
    mcp_servers: list[UUID] = Field(default_factory=list)  # MCPServerDB UUIDs — tools discovered at runtime
    schedules: list[str] = Field(default_factory=list)  # cron strings; stretch (P6)
    channels: list[ChannelBinding] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


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


