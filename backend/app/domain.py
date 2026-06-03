"""Domain schemas: AgentConfig, LLMConfig, MemoryConfig, RunEvent.

ORM models live in db/models.py; these are the Pydantic contracts for
serialization boundaries (HTTP bodies, SSE events, JSON config blobs).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Base(BaseModel):
    # extra="ignore": stored config blobs predate some fields (or carry fields we've
    # since dropped, e.g. AgentConfig.id/created_at/updated_at). Silently drop unknowns
    # rather than failing model_validate when loading an existing row.
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


Provider = Literal["openai", "anthropic", "google", "vllm"]


class LLMConfig(_Base):
    """LLM client config. `provider` picks the underlying langchain client.

    - openai / vllm: ChatOpenAI (vllm = openai-compatible endpoint, custom base_url).
    - anthropic: ChatAnthropic — ignores base_url.
    - google: ChatGoogleGenerativeAI — ignores base_url; api_key is the Gemini key.
    """

    provider: Provider = "openai"
    base_url: str = ""  # required for openai-custom and vllm; ignored for anthropic/google
    api_key: str = ""
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


class ChannelBinding(_Base):
    channel: Literal["slack", "web"]
    external_id: str | None = None  # e.g., slack channel id


class AgentConfig(_Base):
    # No id/created_at/updated_at — AgentDB row holds those authoritatively.
    name: str
    role: str
    description: str | None = None
    system_prompt: str
    llm: LLMConfig
    tools: list[str] = Field(default_factory=list)  # tool names from app.runtime.tools.REGISTRY
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    limits: Limits = Field(default_factory=Limits)
    subagents: list[UUID] = Field(default_factory=list)  # agent UUIDs wrapped as tools at runtime
    skills: list[UUID] = Field(default_factory=list)  # SkillDB UUIDs — content injected into prompt at runtime
    mcp_servers: list[UUID] = Field(default_factory=list)  # MCPServerDB UUIDs — tools discovered at runtime
    channels: list[ChannelBinding] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


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


