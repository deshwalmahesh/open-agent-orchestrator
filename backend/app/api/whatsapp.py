"""WhatsApp (Twilio) configuration API + public webhook.

Per-user Twilio credentials — multiple platform users can connect their own
Twilio accounts simultaneously. The single /whatsapp/webhook endpoint routes
by AccountSid in the POST body.

Endpoints:
  GET  /whatsapp/status     (JWT) — connection status + computed webhook URL
  POST /whatsapp/connect    (JWT) — save credentials, pre-warm adapter cache
  POST /whatsapp/disconnect (JWT) — clear credentials, remove from cache
  POST /whatsapp/active     (JWT) — swap pipeline binding
  POST /whatsapp/webhook    (PUBLIC) — Twilio inbound messages
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_async_session, get_session_factory
from app.db.models import AgentDB, UserDB
from app.db.repos import get_agent
from app.integrations.channels.whatsapp_adapter import WhatsAppAdapter, handle_whatsapp_message
from app.users import current_active_user

log = structlog.get_logger()

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

# ---------------------------------------------------------------------------
# Adapter cache: dict[account_sid → WhatsAppAdapter], lazily populated
# ---------------------------------------------------------------------------
_adapters: dict[str, WhatsAppAdapter] = {}

# Message dedup: bounded set of recently seen MessageSids (prevents Twilio retries)
_DEDUP_MAX = 2000
_seen_message_sids: OrderedDict[str, None] = OrderedDict()


def _dedup_check(message_sid: str) -> bool:
    """Return True if this MessageSid was already processed."""
    if message_sid in _seen_message_sids:
        return True
    _seen_message_sids[message_sid] = None
    while len(_seen_message_sids) > _DEDUP_MAX:
        _seen_message_sids.popitem(last=False)
    return False


def get_or_create_adapter(account_sid: str, auth_token: str, from_number: str) -> WhatsAppAdapter:
    """Return cached adapter or create + cache a new one."""
    if account_sid in _adapters:
        return _adapters[account_sid]
    adapter = WhatsAppAdapter(account_sid, auth_token, from_number)
    _adapters[account_sid] = adapter
    return adapter


async def warm_adapters_from_db() -> int:
    """Pre-warm adapter cache from all users with saved Twilio credentials.
    Called once at startup from lifespan."""
    sf = get_session_factory()
    count = 0
    async with sf() as session:
        stmt = select(UserDB).where(UserDB.whatsapp_account_sid.isnot(None))
        users = (await session.execute(stmt)).scalars().all()
        for u in users:
            if u.whatsapp_account_sid and u.whatsapp_auth_token and u.whatsapp_from_number:
                get_or_create_adapter(u.whatsapp_account_sid, u.whatsapp_auth_token, u.whatsapp_from_number)
                count += 1
    if count:
        log.info("whatsapp.adapters_warmed", count=count)
    return count


# ---------------------------------------------------------------------------
# Pydantic bodies
# ---------------------------------------------------------------------------


class WhatsAppConnectBody(BaseModel):
    account_sid: str
    auth_token: str
    from_number: str  # "whatsapp:+14155238886"
    webhook_base_url: str | None = None
    agent_id: str | None = None


class WhatsAppActiveBody(BaseModel):
    agent_id: str


# ---------------------------------------------------------------------------
# Pipeline binding helpers (mirrors slack.py pattern)
# ---------------------------------------------------------------------------


async def _apply_single_whatsapp_binding(
    session: AsyncSession, *, user_id: UUID, active_agent_id: UUID
) -> None:
    """Enforce one-active-pipeline-at-a-time for WhatsApp."""
    rows = (await session.execute(
        select(AgentDB).where(AgentDB.user_id == user_id)
    )).scalars().all()
    for row in rows:
        cfg = dict(row.config or {})
        channels = list(cfg.get("channels") or [])
        non_wa = [c for c in channels if c.get("channel") != "whatsapp"]
        if row.id == active_agent_id:
            new_channels = [*non_wa, {"channel": "whatsapp", "external_id": ""}]
        else:
            new_channels = non_wa
        if new_channels != channels:
            cfg["channels"] = new_channels
            row.config = cfg
    await session.commit()


async def _active_whatsapp_agent_id(session: AsyncSession, *, user_id: UUID) -> UUID | None:
    rows = (await session.execute(
        select(AgentDB).where(AgentDB.user_id == user_id)
    )).scalars().all()
    for row in rows:
        for c in (row.config or {}).get("channels") or []:
            if c.get("channel") == "whatsapp":
                return row.id
    return None


def _compute_webhook_url(user: UserDB) -> str:
    base = (user.webhook_base_url or "").rstrip("/") or get_settings().base_url.rstrip("/")
    return f"{base}/whatsapp/webhook"


# ---------------------------------------------------------------------------
# JWT-protected endpoints
# ---------------------------------------------------------------------------


@router.get("/status")
async def status(
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    connected = bool(
        user.whatsapp_account_sid and user.whatsapp_auth_token and user.whatsapp_from_number
    )
    active = await _active_whatsapp_agent_id(session, user_id=user.id)
    return {
        "connected": connected,
        "active_agent_id": str(active) if active else None,
        "webhook_url": _compute_webhook_url(user) if connected else None,
        "from_number": user.whatsapp_from_number if connected else None,
    }


@router.post("/connect")
async def connect(
    body: WhatsAppConnectBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    """Save Twilio credentials. Does NOT clear other users' creds (multi-user)."""
    user.whatsapp_account_sid = body.account_sid
    user.whatsapp_auth_token = body.auth_token
    user.whatsapp_from_number = body.from_number
    if body.webhook_base_url is not None:
        user.webhook_base_url = body.webhook_base_url.rstrip("/") or None
    await session.commit()

    # Pre-warm adapter cache
    get_or_create_adapter(body.account_sid, body.auth_token, body.from_number)

    # Pipeline binding
    if body.agent_id:
        try:
            agent_uuid = UUID(body.agent_id)
        except ValueError:
            agent_uuid = None
        if agent_uuid is not None:
            target = await get_agent(session, agent_id=agent_uuid, user_id=user.id)
            if target is None:
                raise HTTPException(status_code=404, detail="agent not found")
            if target.deployed_at is None:
                raise HTTPException(status_code=400, detail="pipeline is in Draft — deploy it before binding to WhatsApp")
            await _apply_single_whatsapp_binding(session, user_id=user.id, active_agent_id=agent_uuid)

    active = await _active_whatsapp_agent_id(session, user_id=user.id)
    return {
        "connected": True,
        "active_agent_id": str(active) if active else None,
        "webhook_url": _compute_webhook_url(user),
        "from_number": user.whatsapp_from_number,
    }


@router.post("/active")
async def set_active(
    body: WhatsAppActiveBody,
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    try:
        agent_uuid = UUID(body.agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid agent_id")
    target = await get_agent(session, agent_id=agent_uuid, user_id=user.id)
    if target is None:
        raise HTTPException(status_code=404, detail="agent not found")
    if target.deployed_at is None:
        raise HTTPException(status_code=400, detail="pipeline is in Draft — deploy it before binding to WhatsApp")
    await _apply_single_whatsapp_binding(session, user_id=user.id, active_agent_id=agent_uuid)
    active = await _active_whatsapp_agent_id(session, user_id=user.id)
    return {"active_agent_id": str(active) if active else None}


@router.post("/disconnect")
async def disconnect(
    user: Annotated[UserDB, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> dict:
    old_sid = user.whatsapp_account_sid
    user.whatsapp_account_sid = None
    user.whatsapp_auth_token = None
    user.whatsapp_from_number = None
    await session.commit()
    if old_sid and old_sid in _adapters:
        del _adapters[old_sid]
    return {"connected": False}


# ---------------------------------------------------------------------------
# Public webhook — Twilio POSTs here. No JWT.
# ---------------------------------------------------------------------------

# Empty TwiML response — Twilio expects valid XML or empty 200
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.post("/webhook")
async def webhook(request: Request) -> PlainTextResponse:
    """Handle inbound WhatsApp message from Twilio.

    Returns empty TwiML immediately; actual processing + reply happens in a
    background task (Twilio has a 15s webhook timeout).
    """
    form = await request.form()
    params = dict(form)

    account_sid = params.get("AccountSid", "")
    message_sid = params.get("MessageSid", "")
    from_phone = params.get("From", "")
    body_text = (params.get("Body") or "").strip()
    profile_name = params.get("ProfileName", "")
    num_media = int(params.get("NumMedia", "0") or "0")

    if not account_sid or not from_phone:
        return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")

    # Dedup — Twilio retries on timeout
    if message_sid and _dedup_check(message_sid):
        log.debug("whatsapp.dedup", message_sid=message_sid)
        return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")

    # Look up adapter by AccountSid (cache → DB fallback)
    adapter = _adapters.get(account_sid)
    if adapter is None:
        sf = get_session_factory()
        async with sf() as session:
            owner = (await session.execute(
                select(UserDB).where(UserDB.whatsapp_account_sid == account_sid)
            )).scalar_one_or_none()
            if owner and owner.whatsapp_auth_token and owner.whatsapp_from_number:
                adapter = get_or_create_adapter(
                    account_sid, owner.whatsapp_auth_token, owner.whatsapp_from_number
                )

    if adapter is None:
        log.warning("whatsapp.webhook.unknown_account", account_sid=account_sid)
        return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")

    # Signature validation
    signature = request.headers.get("X-Twilio-Signature", "")
    # Use the owner's webhook_base_url for validation (not request.url which may differ behind proxy)
    sf = get_session_factory()
    async with sf() as session:
        owner = (await session.execute(
            select(UserDB).where(UserDB.whatsapp_account_sid == account_sid)
        )).scalar_one_or_none()
    validation_url = _compute_webhook_url(owner) if owner else str(request.url)

    if not adapter.validate_signature(validation_url, params, signature):
        log.warning("whatsapp.webhook.invalid_signature", account_sid=account_sid)
        return PlainTextResponse("Forbidden", status_code=403)

    # Media messages — text only for v1
    if num_media > 0 and not body_text:
        asyncio.create_task(
            adapter.send_message(to=from_phone, body="I can only handle text messages for now.")
        )
        return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")

    if not body_text:
        return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")

    # Dispatch in background — return 200 immediately (Twilio 15s timeout)
    asyncio.create_task(
        handle_whatsapp_message(
            account_sid,
            from_phone,
            body_text,
            profile_name,
            adapter=adapter,
            session_factory=get_session_factory(),
        )
    )

    return PlainTextResponse(_EMPTY_TWIML, media_type="application/xml")
