"""Slack Bolt Socket Mode single platform bot.

Inbound DM → look up `users.slack_user_id` (set explicitly via PATCH /users/me;
no auto-provision in v1) → look up user's newest agent → find/create a chat keyed
by (channel_id, thread_ts) → trigger run → poll for completion → post reply on
the same thread.

Adapter starts in the lifespan if both SLACK_BOT_TOKEN and SLACK_APP_TOKEN are set.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable
from uuid import UUID

import structlog
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import get_session_factory
from app.db.models import AgentDB, ChatDB, RunDB, UserDB
from app.db.repos import create_chat, list_messages
from app.services.run_service import start_run

log = structlog.get_logger()


async def _find_user_by_slack(session: AsyncSession, slack_uid: str) -> UserDB | None:
    stmt = select(UserDB).where(UserDB.slack_user_id == slack_uid)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _slack_agent(session: AsyncSession, user_id: UUID) -> AgentDB | None:
    """Return the agent to route a Slack message to.

    Priority:
    1. Any agent with an explicit Slack channel binding (user designated it for Slack).
    2. Fall back to most recently updated agent.
    """
    stmt = select(AgentDB).where(AgentDB.user_id == user_id)
    agents = (await session.execute(stmt)).scalars().all()
    for ag in agents:
        channels = (ag.config or {}).get("channels", [])
        if any(c.get("channel") == "slack" for c in channels):
            return ag
    # Fallback: most recently saved/deployed agent
    stmt = (
        select(AgentDB)
        .where(AgentDB.user_id == user_id)
        .order_by(AgentDB.updated_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _find_or_create_chat(
    session: AsyncSession, *, user_id: UUID, agent_id: UUID, channel: str, thread_ts: str
) -> ChatDB:
    """Same Slack thread + same active pipeline → reuse the Chat (continue conversation).
    Pipeline switch (different agent_id under same thread) → new Chat (the user-spec'd
    behavior: switching pipeline always starts a new conversation, even mid-thread)."""
    ext_id = f"{channel}:{thread_ts}"
    stmt = select(ChatDB).where(
        ChatDB.user_id == user_id,
        ChatDB.channel == "slack",
        ChatDB.external_thread_id == ext_id,
        ChatDB.agent_id == agent_id,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing
    return await create_chat(
        session,
        user_id=user_id,
        agent_id=agent_id,
        channel="slack",
        external_thread_id=ext_id,
        title=f"Slack {channel}",
    )


async def wait_for_reply(run_id: UUID, *, timeout: float = 60.0) -> tuple[str, str | None]:
    """Poll the run status until terminal; return (status, reply_text).

    status ∈ {"succeeded", "failed", "timeout"}; reply_text is the agent's text
    (may be empty string on a succeeded run, None on failure/timeout). The caller
    decides how to present empty vs failed vs timeout — they're distinct UX cases.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    sf = get_session_factory()
    while loop.time() < deadline:
        async with sf() as session:
            run = await session.get(RunDB, run_id)
            if run is not None and run.status in ("succeeded", "failed"):
                if run.status == "failed":
                    return ("failed", run.error)
                rows = await list_messages(session, chat_id=run.chat_id)
                for m in reversed(rows):
                    if m.sender != "user":
                        return ("succeeded", m.content)
                return ("succeeded", "")
        await asyncio.sleep(0.25)
    return ("timeout", None)


async def handle_slack_message(
    event: dict,
    say: Callable[..., Awaitable[Any]],
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Pure dispatcher — easy to unit-test by passing a mock `say`.

    Reply policy:
      - Unknown slack_user_id → one-shot help (no DB writes).
      - Known user without agent → tell them to create one.
      - Otherwise → trigger run, wait, post reply or timeout message.
    """
    if event.get("bot_id"):
        return  # ignore our own echoes
    text = (event.get("text") or "").strip()
    if not text:
        return
    slack_uid = event.get("user")
    channel = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")
    if not (slack_uid and channel and thread_ts):
        log.warning("slack.event.malformed", event=event)
        return
    log.info(
        "slack.inbound", slack_user_id=slack_uid, channel=channel, thread_ts=thread_ts,
        text_preview=text[:80],
    )

    async with session_factory() as session:
        user = await _find_user_by_slack(session, slack_uid)
        if user is None:
            await say(
                thread_ts=thread_ts,
                text=(
                    "I don't recognise this Slack account yet. "
                    "Open the web app → Integrations → Link Slack, paste your Slack member ID "
                    f"(yours is `{slack_uid}`), and I'll route your messages from then on."
                ),
            )
            return

        agent = await _slack_agent(session, user.id)
        if agent is None:
            await say(
                thread_ts=thread_ts,
                text="You don't have any deployed pipelines yet. Create one in the web app — Pipelines → New Pipeline → Deploy.",
            )
            return

        chat = await _find_or_create_chat(
            session, user_id=user.id, agent_id=agent.id, channel=channel, thread_ts=thread_ts
        )
        # Agent was deleted since the chat was created — reassign to newest.
        if chat.agent_id is None:
            chat.agent_id = agent.id
            await session.commit()
        run_id = await start_run(session, chat_id=chat.id, user_text=text)

    status, reply = await wait_for_reply(run_id, timeout=120.0)
    if status == "timeout":
        out = "Still working on that — taking longer than usual. Try again in a moment."
    elif status == "failed":
        out = f"That run failed: {reply or 'unknown error'}"
    elif not reply or not reply.strip():
        out = (
            "I produced an empty reply — usually means the token budget was spent "
            "on reasoning before any output. Try a simpler prompt, or increase the "
            "agent's `max_tokens`."
        )
    else:
        out = reply
    await say(thread_ts=thread_ts, text=out)


class SlackAdapter:
    def __init__(self, bot_token: str, app_token: str) -> None:
        self.app = AsyncApp(token=bot_token)
        self.handler = AsyncSocketModeHandler(self.app, app_token)

        @self.app.event("message")
        async def _on_message(event, say):
            await handle_slack_message(event, say, session_factory=get_session_factory())

    async def start(self) -> None:
        log.info("slack.connect")
        await self.handler.connect_async()

    async def stop(self) -> None:
        log.info("slack.disconnect")
        await self.handler.close_async()
