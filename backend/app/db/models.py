"""All ORM tables in one file. AgentDB/WorkflowDB store the full Pydantic config
as JSON — queryable columns are only the bits the API/UI sort or filter on."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.domain import utcnow


class UserDB(SQLAlchemyBaseUserTableUUID, Base):
    """fastapi-users base provides: id (UUID), email, hashed_password,
    is_active, is_superuser, is_verified. We add display name + slack mapping."""

    name: Mapped[str] = mapped_column(String(120), default="")
    slack_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)


class AgentDB(Base):
    __tablename__ = "agents"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSON)  # full AgentConfig dump
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WorkflowDB(Base):
    __tablename__ = "workflows"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    # Null user_id = global template (seeded). Real workflows have user_id.
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(120))
    is_template: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    definition: Mapped[dict] = mapped_column(JSON)  # full WorkflowDef dump
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PersonaDB(Base):
    """Named system_prompt. user_id IS NULL = global (visible to all, read-only).
    Otherwise owned by that user. Same pattern as workflow templates."""

    __tablename__ = "personas"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(120))
    system_prompt: Mapped[str] = mapped_column(Text)


class ChatDB(Base):
    __tablename__ = "chats"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    persona_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("personas.id", ondelete="SET NULL"), nullable=True
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
    workflow_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_tokens: Mapped[dict] = mapped_column(JSON, default=dict)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
