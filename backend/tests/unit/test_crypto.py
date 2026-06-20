"""Phase 3a: secrets-at-rest encryption (Fernet TypeDecorators)."""
from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet, MultiFernet

from app import crypto
from app.crypto import EncryptedJSON, EncryptedStr


@pytest.fixture
def _with_key(monkeypatch):
    """Force a real cipher regardless of env/settings cache."""
    cipher = MultiFernet([Fernet(Fernet.generate_key())])
    monkeypatch.setattr(crypto, "_cipher", lambda: cipher)
    return cipher


# --- decorator-level round-trip ---

def test_encrypted_str_roundtrip_and_ciphertext(_with_key):
    col = EncryptedStr()
    stored = col.process_bind_param("xoxb-super-secret", None)
    assert stored != "xoxb-super-secret"          # not plaintext at rest
    assert "secret" not in stored                  # the token doesn't leak
    assert col.process_result_value(stored, None) == "xoxb-super-secret"


def test_encrypted_json_roundtrip_and_ciphertext(_with_key):
    col = EncryptedJSON()
    blob = {"llm": {"api_key": "sk-live-123"}, "name": "agent"}
    stored = col.process_bind_param(blob, None)
    assert isinstance(stored, str) and "sk-live-123" not in stored
    assert col.process_result_value(stored, None) == blob


def test_none_passes_through(_with_key):
    assert EncryptedStr().process_bind_param(None, None) is None
    assert EncryptedStr().process_result_value(None, None) is None
    assert EncryptedJSON().process_bind_param(None, None) is None
    assert EncryptedJSON().process_result_value(None, None) is None


# --- migration tolerance: enabling encryption on a DB with existing plaintext ---

def test_decrypt_tolerates_legacy_plaintext(_with_key):
    # A row written before encryption was enabled is plaintext, not a Fernet token.
    assert EncryptedStr().process_result_value("legacy-plain-token", None) == "legacy-plain-token"
    assert EncryptedJSON().process_result_value('{"k": "v"}', None) == {"k": "v"}
    # Postgres legacy json column hands back an already-parsed dict.
    assert EncryptedJSON().process_result_value({"k": "v"}, None) == {"k": "v"}


# --- no key configured (dev/test) = passthrough, no encryption ---

def test_passthrough_when_no_key(monkeypatch):
    monkeypatch.setattr(crypto, "_cipher", lambda: None)
    assert EncryptedStr().process_bind_param("plain", None) == "plain"
    assert EncryptedJSON().process_bind_param({"k": "v"}, None) == '{"k": "v"}'
    assert EncryptedJSON().process_result_value('{"k": "v"}', None) == {"k": "v"}


# --- end-to-end DB proof: the agent config sits encrypted in the column ---

@pytest.mark.asyncio
async def test_agent_config_encrypted_at_rest(monkeypatch):
    from sqlalchemy import text

    from app.db import create_all, get_session_factory
    from app.db.models import AgentDB, UserDB

    cipher = MultiFernet([Fernet(Fernet.generate_key())])
    monkeypatch.setattr(crypto, "_cipher", lambda: cipher)

    await create_all()
    sf = get_session_factory()
    secret_cfg = {"llm": {"api_key": "sk-at-rest-XYZ", "model": "m"}, "name": "A"}
    async with sf() as s:
        user = UserDB(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@b.c",
                      hashed_password="x", is_active=True)
        s.add(user)
        await s.flush()
        agent = AgentDB(user_id=user.id, name="A", config=secret_cfg)
        s.add(agent)
        await s.commit()
        agent_id = agent.id

    # Raw column read bypasses the decorator → must be ciphertext (no plaintext key).
    async with sf() as s:
        raw = (await s.execute(text("SELECT config FROM agents"))).scalar_one()
        assert "sk-at-rest-XYZ" not in raw
        assert cipher.decrypt(raw.encode())  # genuinely Fernet-decryptable

    # ORM read transparently decrypts back to the original dict.
    async with sf() as s:
        loaded = await s.get(AgentDB, agent_id)
        assert loaded.config == secret_cfg
