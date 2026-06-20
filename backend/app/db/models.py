"""All ORM tables in one file. AgentDB stores the full Pydantic config
as JSON — queryable columns are only the bits the API/UI sort or filter on."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.domain import utcnow


class UserDB(SQLAlchemyBaseUserTableUUID, Base):
    """fastapi-users base provides: id (UUID), email, hashed_password,
    is_active, is_superuser, is_verified. We add display name + slack mapping."""

    name: Mapped[str] = mapped_column(String(120), default="")
    slack_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    # "free" | "paid" | "admin" — controls auto-seeded keys and rate limits
    plan: Mapped[str] = mapped_column(String(20), default="free", server_default="free")
    # Per-user Slack bot tokens; first user to connect becomes the platform Slack bot
    slack_bot_token: Mapped[str | None] = mapped_column(String(200), nullable=True)
    slack_app_token: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Per-user Twilio WhatsApp credentials (multi-user concurrent, unlike Slack)
    whatsapp_account_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    whatsapp_auth_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    whatsapp_from_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Public base URL for webhook signature validation; set after first deploy
    webhook_base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)


class AgentDB(Base):
    __tablename__ = "agents"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSON)  # full AgentConfig dump
    # NULL = Draft (cannot be used in chats / Slack). Set by POST /agents/{id}/deploy.
    # Edits after deploy do NOT reset this — explicit user choice.
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)



class PersonaDB(Base):
    """Named system_prompt. user_id IS NULL = global (visible to all, read-only).
    Otherwise owned by that user."""

    __tablename__ = "personas"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(120))
    system_prompt: Mapped[str] = mapped_column(Text)


class SkillDB(Base):
    """Reusable knowledge/instruction document. Injected into agent system_prompt at runtime.
    user_id IS NULL = global (read-only). Same ownership pattern as PersonaDB."""

    __tablename__ = "skills"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserToolConfigDB(Base):
    """Per-user, per-tool credentials (e.g. Tavily API key). Composite unique on (user_id, tool_name)."""

    __tablename__ = "user_tool_configs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MCPServerDB(Base):
    """Registered MCP server connection. Tools discovered at runtime via GET /mcp-servers/{id}/tools."""

    __tablename__ = "mcp_servers"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(500))
    transport: Mapped[str] = mapped_column(String(20), default="http")
    headers: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatDB(Base):
    __tablename__ = "chats"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), default="web")
    external_thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Rolling summary of the oldest `summary_count` messages. Updated on threshold cross.
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RunDB(Base):
    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    chat_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    # Lifecycle: queued → running → succeeded | failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_tokens: Mapped[dict] = mapped_column(JSON, default=dict)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stable machine code from app.errors (e.g. RATE_LIMITED). NULL on success.
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Per-run tool/sub-agent call counts, e.g. {"web_search": 3, "ResearchBot": 1}.
    # Populated by the UsageCounter callback; aggregated per-user for usage stats.
    tool_calls: Mapped[dict] = mapped_column(JSON, default=dict)


class MessageDB(Base):
    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    chat_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sender: Mapped[str] = mapped_column(String(64))  # "user" | "system" | agent_id as str
    recipient: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RunEventDB(Base):
    """Composite PK (run_id, seq) — natural ordering, no surrogate id needed."""

    __tablename__ = "run_events"

    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    type: Mapped[str] = mapped_column(String(40), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class FeedbackDB(Base):
    """User thumbs up/down on a run, with an optional comment. One row per (user, run)
    — re-submitting updates it. Minimal foundation for the metrics dashboard."""

    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("user_id", "run_id", name="uq_feedback_user_run"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    rating: Mapped[str] = mapped_column(String(4))  # "up" | "down"
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
