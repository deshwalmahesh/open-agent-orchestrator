"""Per-test SQLite isolation + shared fixtures. Live-LLM tests still read VLLM_*/TAVILY_* from .env."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import get_session_factory
from app.db.models import PersonaDB
from app.main import create_app


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    # ≥32 bytes so HMAC-SHA256 doesn't emit InsecureKeyLengthWarning during tests.
    monkeypatch.setenv("JWT_SECRET", "test-secret-32-bytes-long-for-hmac-sha256")
    # Dedicated Redis DB (15) so tests never touch dev data (db 0), and flush it so
    # cross-test state (dedup keys, leader locks, pub/sub) can't leak between tests.
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    get_settings.cache_clear()

    import app.db as db_module
    db_module._engine = None
    db_module._session_factory = None

    import app.redis_client as redis_module
    redis_module._redis = None

    # Disable the slowapi IP rate limiter in tests — its in-memory counter is a
    # module global that accumulates across the whole suite (120+ /auth/register
    # calls from one test-client IP would trip 60/min). Not under test here.
    from app.main import limiter
    limiter.enabled = False

    try:
        import redis
        redis.Redis.from_url("redis://localhost:6379/15").flushdb()
    except Exception:
        pass  # no Redis reachable → gated tests skip anyway

    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    """TestClient with lifespan (runs create_all on the isolated test DB)."""
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def signup_and_login(client):
    """Factory: signup_and_login(email='alice@example.com') -> token. Reuses the fixture's client."""
    def _do(email: str = "alice@example.com", name: str = "Alice") -> str:
        r = client.post(
            "/auth/register", json={"email": email, "password": "longenoughpwd123", "name": name}
        )
        assert r.status_code == 201, r.text
        r = client.post(
            "/auth/jwt/login", data={"username": email, "password": "longenoughpwd123"}
        )
        assert r.status_code == 200, r.text
        return r.json()["access_token"]
    return _do


@pytest.fixture
def auth_header():
    def _do(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}
    return _do


@pytest.fixture
def sample_agent_config():
    """Minimal-valid AgentConfig payload for POST /agents. Override fields as needed."""
    def _do(name: str = "Researcher", **overrides) -> dict:
        base = {
            "name": name,
            "role": "researcher",
            "system_prompt": "You are a researcher.",
            "llm": {"base_url": "http://vllm.local/v1", "model": "qwen3-coder"},
            "tools": [],
        }
        base.update(overrides)
        return base
    return _do


@pytest.fixture
def insert_global_persona():
    """Insert a global persona (user_id=NULL) directly into the test DB —
    emulates the startup seed that lands in A6c."""
    def _do(name: str = "Concise", prompt: str = "Be concise.") -> str:
        async def _go() -> str:
            async with get_session_factory()() as s:
                row = PersonaDB(user_id=None, name=name, system_prompt=prompt)
                s.add(row)
                await s.commit()
                await s.refresh(row)
                return str(row.id)
        return asyncio.run(_go())
    return _do
