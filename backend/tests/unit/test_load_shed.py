"""D2: global load-shedding. _check_load_shed raises QueueFull once the arq
backlog (ZCARD of the queue) reaches max_queue_depth; no-op otherwise."""
import pytest

from app.services import run_service
from app.services.run_service import QueueFull, _check_load_shed


class _Pool:
    def __init__(self, depth):
        self._depth = depth

    async def zcard(self, key):
        return self._depth


class _S:
    def __init__(self, depth_cap):
        self.run_executor = "queue"
        self.max_queue_depth = depth_cap


@pytest.mark.asyncio
async def test_no_shed_when_disabled(monkeypatch):
    monkeypatch.setattr(run_service, "_ARQ_POOL", _Pool(9999))
    monkeypatch.setattr(run_service, "get_settings", lambda: _S(depth_cap=0))  # 0 = off
    await _check_load_shed()  # must not raise


@pytest.mark.asyncio
async def test_no_shed_under_cap(monkeypatch):
    monkeypatch.setattr(run_service, "_ARQ_POOL", _Pool(5))
    monkeypatch.setattr(run_service, "get_settings", lambda: _S(depth_cap=10))
    await _check_load_shed()  # 5 < 10 → fine


@pytest.mark.asyncio
async def test_sheds_at_cap(monkeypatch):
    monkeypatch.setattr(run_service, "_ARQ_POOL", _Pool(10))
    monkeypatch.setattr(run_service, "get_settings", lambda: _S(depth_cap=10))
    with pytest.raises(QueueFull):
        await _check_load_shed()  # 10 >= 10 → shed


@pytest.mark.asyncio
async def test_no_shed_inline_mode(monkeypatch):
    # Inline mode (no pool) never sheds regardless of cap.
    monkeypatch.setattr(run_service, "_ARQ_POOL", None)
    monkeypatch.setattr(run_service, "get_settings", lambda: _S(depth_cap=1))
    await _check_load_shed()  # must not raise
