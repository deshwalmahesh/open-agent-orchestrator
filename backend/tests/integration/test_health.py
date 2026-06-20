def test_health_returns_ok(client):
    assert client.get("/health").json() == {"status": "ok"}


class _FakeRedis:
    def __init__(self, ok: bool):
        self._ok = ok

    async def ping(self):
        if not self._ok:
            raise ConnectionError("redis down")
        return True


def test_ready_ok_when_deps_up(client, monkeypatch):
    # DB is real (sqlite); fake a healthy Redis ping so this needs no running Redis.
    monkeypatch.setattr("app.api.health.get_redis", lambda: _FakeRedis(True))
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["redis"] == "ok" and body["checks"]["db"] == "ok"


def test_ready_503_when_redis_down(client, monkeypatch):
    monkeypatch.setattr("app.api.health.get_redis", lambda: _FakeRedis(False))
    r = client.get("/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"].startswith("error")
    assert body["checks"]["db"] == "ok"  # DB still fine — only Redis is down


class _FakeRedisDepth:
    def __init__(self, depth):
        self._depth = depth

    async def zcard(self, key):
        return self._depth


def test_queue_depth_reports_zcard(client, monkeypatch):
    monkeypatch.setattr("app.api.health.get_redis", lambda: _FakeRedisDepth(42))
    r = client.get("/metrics/queue-depth")
    assert r.status_code == 200
    assert r.json() == {"depth": 42}


def test_queue_depth_zero_when_redis_down(client, monkeypatch):
    class _Boom:
        async def zcard(self, key):
            raise ConnectionError("redis down")
    monkeypatch.setattr("app.api.health.get_redis", lambda: _Boom())
    r = client.get("/metrics/queue-depth")
    assert r.status_code == 200
    assert r.json() == {"depth": 0}  # scale-to-floor, not crash


def test_tools_returns_list(client):
    r = client.get("/tools")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    assert "calculator" in names


# --- 3d: prod refuses the placeholder JWT secret (startup fail-fast) ---

async def test_prod_refuses_default_jwt_secret(monkeypatch):
    import pytest

    from app.config import get_settings
    from app.main import create_app, lifespan

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", "CHANGE_ME_IN_PROD")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            async with lifespan(create_app()):
                pass
    finally:
        get_settings.cache_clear()
