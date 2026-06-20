"""Twilio WhatsApp adapter — per-user, stateless REST client.

Unlike Slack (single persistent Socket Mode connection), WhatsApp is webhook-based:
Twilio POSTs inbound messages, we reply via the REST API. Multiple platform users
can connect their own Twilio accounts simultaneously — the webhook routes by
AccountSid in the POST body.

External contacts (customers) message the bot owner's pipeline. Each contact gets
their own chat thread keyed by phone number.
"""
from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from twilio.http.async_http_client import AsyncTwilioHttpClient
from twilio.request_validator import RequestValidator
from twilio.rest import Client

from app.db.models import AgentDB, ChatDB, UserDB
from app.db.repos import create_chat
from app.services.run_service import start_run

log = structlog.get_logger()

# WhatsApp message body limit — chunk long replies below this
WA_MAX_CHARS = 4096
_CHUNK_SIZE = 4000  # leave margin for Twilio overhead


class WhatsAppAdapter:
    """Stateless Twilio WhatsApp client. One instance per connected user, cached in memory."""

    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self.account_sid = account_sid
        self.from_number = from_number
        self._http_client = AsyncTwilioHttpClient()
        self.client = Client(account_sid, auth_token, http_client=self._http_client)
        self.validator = RequestValidator(auth_token)

    def validate_signature(self, url: str, params: dict, signature: str) -> bool:
        return self.validator.validate(url, params, signature)

    async def send_message(self, to: str, body: str) -> list[str]:
        """Send one or more WhatsApp messages (chunks if body > 4096 chars).

        Returns list of MessageSid strings.
        """
        chunks = _chunk_text(body)
        sids: list[str] = []
        for chunk in chunks:
            msg = await self.client.messages.create_async(
                from_=self.from_number,
                to=to,
                body=chunk,
            )
            sids.append(msg.sid)
        return sids


def _chunk_text(text: str) -> list[str]:
    """Split text into chunks respecting WhatsApp's 4096-char limit."""
    if len(text) <= WA_MAX_CHARS:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:_CHUNK_SIZE])
        text = text[_CHUNK_SIZE:]
    return chunks


# ---------------------------------------------------------------------------
# Routing helpers (simpler than Slack — no identity-based routing)
# ---------------------------------------------------------------------------


async def _find_owner_by_account_sid(session: AsyncSession, account_sid: str) -> UserDB | None:
    """Find the platform user who owns this Twilio AccountSid."""
    stmt = select(UserDB).where(UserDB.whatsapp_account_sid == account_sid)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _whatsapp_agent(session: AsyncSession, user_id: UUID) -> AgentDB | None:
    """Return the agent to route a WhatsApp message to.

    Priority:
    1. Any agent with an explicit whatsapp channel binding.
    2. Fall back to most recently updated deployed agent.
    """
    stmt = select(AgentDB).where(AgentDB.user_id == user_id)
    agents = (await session.execute(stmt)).scalars().all()
    for ag in agents:
        channels = (ag.config or {}).get("channels", [])
        if any(c.get("channel") == "whatsapp" for c in channels):
            return ag
    # Fallback: most recently updated deployed agent
    stmt = (
        select(AgentDB)
        .where(AgentDB.user_id == user_id, AgentDB.deployed_at.isnot(None))
        .order_by(AgentDB.updated_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _find_or_create_chat(
    session: AsyncSession, *, owner_id: UUID, agent_id: UUID, contact_phone: str
) -> ChatDB:
    """Same contact phone + same pipeline → reuse chat (continue conversation)."""
    stmt = select(ChatDB).where(
        ChatDB.user_id == owner_id,
        ChatDB.channel == "whatsapp",
        ChatDB.external_thread_id == contact_phone,
        ChatDB.agent_id == agent_id,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing
    return await create_chat(
        session,
        user_id=owner_id,
        agent_id=agent_id,
        channel="whatsapp",
        external_thread_id=contact_phone,
        title=f"WhatsApp {contact_phone}",
    )


# ---------------------------------------------------------------------------
# Message handler (called as background task from the webhook)
# ---------------------------------------------------------------------------


async def handle_whatsapp_message(
    account_sid: str,
    from_phone: str,
    body: str,
    profile_name: str,
    *,
    adapter: WhatsAppAdapter,
    session_factory: async_sessionmaker,
) -> None:
    """Dispatch an inbound WhatsApp message to the owner's pipeline and reply."""
    log.info(
        "whatsapp.inbound",
        account_sid=account_sid,
        from_phone=from_phone,
        profile_name=profile_name,
        text_preview=body[:80],
    )

    async with session_factory() as session:
        owner = await _find_owner_by_account_sid(session, account_sid)
        if owner is None:
            log.warning("whatsapp.unknown_account", account_sid=account_sid)
            return

        agent = await _whatsapp_agent(session, owner.id)
        if agent is None:
            log.warning("whatsapp.no_agent", user_id=str(owner.id))
            await adapter.send_message(
                to=from_phone,
                body="No deployed pipeline is configured yet. Please set one up in the web app.",
            )
            return

        chat = await _find_or_create_chat(
            session, owner_id=owner.id, agent_id=agent.id, contact_phone=from_phone
        )
        if chat.agent_id is None:
            chat.agent_id = agent.id
            await session.commit()

        run_id = await start_run(session, chat_id=chat.id, user_text=body)

    # Import from slack_adapter — wait_for_reply is channel-agnostic
    from app.integrations.channels.slack_adapter import wait_for_reply

    status, reply = await wait_for_reply(run_id, timeout=120.0)
    if status == "timeout":
        out = "Still working on that — taking longer than usual. Try again in a moment."
    elif status == "failed":
        # `reply` is the user-facing message from the failure taxonomy (app.errors).
        out = reply or "Something went wrong on our side — please try again."
    elif not reply or not reply.strip():
        out = (
            "I produced an empty reply — usually means the token budget was spent "
            "on reasoning before any output. Try a simpler prompt, or increase the "
            "agent's max_tokens."
        )
    else:
        out = reply

    try:
        await adapter.send_message(to=from_phone, body=out)
    except Exception:
        log.exception("whatsapp.send_failed", to=from_phone)
