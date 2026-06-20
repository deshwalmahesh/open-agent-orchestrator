"""Transparent field encryption at rest (Fernet).

BYOK LLM keys, Slack/Twilio tokens and per-tool API keys must not sit in
plaintext in the DB — a dump or read-replica leak would expose every tenant's
third-party credentials. These TypeDecorators encrypt on write / decrypt on read
so the application code keeps handling plain dicts/strings.

Key(s) come from SECRET_ENCRYPTION_KEYS (comma-separated, newest first).
MultiFernet decrypts with any listed key but always encrypts with the first, so
rotation = prepend a new key and re-save the rows. Unset = passthrough (dev/test
convenience); prod refuses to boot without it (see main.lifespan).

Decrypt is plaintext-tolerant: a value that isn't valid ciphertext is returned
as-is, so enabling encryption on a DB that already has plaintext rows is safe —
those rows transparently re-encrypt the next time they're written.

Swapping Fernet for a cloud KMS later touches only this file.
"""
from __future__ import annotations

import json
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import get_settings


@lru_cache(maxsize=1)  # settings are process-fixed; build the cipher once
def _cipher() -> MultiFernet | None:
    raw = get_settings().secret_encryption_keys
    keys = [k.strip() for k in (raw or "").split(",") if k.strip()]
    return MultiFernet([Fernet(k) for k in keys]) if keys else None


def _encrypt(plaintext: str) -> str:
    c = _cipher()
    return c.encrypt(plaintext.encode()).decode() if c else plaintext


def _decrypt(token: str) -> str:
    c = _cipher()
    if c is None:
        return token
    try:
        return c.decrypt(token.encode()).decode()
    except InvalidToken:
        return token  # legacy plaintext row written before encryption was enabled


class EncryptedStr(TypeDecorator):
    """A text column whose value is Fernet-encrypted at rest."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else _encrypt(value)

    def process_result_value(self, value, dialect):
        return None if value is None else _decrypt(value)


class EncryptedJSON(TypeDecorator):
    """A JSON blob serialized then Fernet-encrypted at rest (opaque ciphertext)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else _encrypt(json.dumps(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value  # legacy json-typed column (pre-encryption), already parsed by the driver
        return json.loads(_decrypt(value))
