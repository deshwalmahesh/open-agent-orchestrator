"""Phase 4: LLM resilience. invoke_with_retry retries only taxonomy-retryable
errors (429 / 5xx / transient I/O) and fails fast on AUTH / INPUT_INVALID."""
import asyncio

import pytest

from app.llm import (
    ProviderCircuitOpen,
    _Breaker,
    _breakers,
    _is_retryable,
    invoke_with_breaker,
    invoke_with_retry,
)


class _Err(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


def test_is_retryable_maps_through_taxonomy():
    assert _is_retryable(_Err(429)) is True          # RATE_LIMITED
    assert _is_retryable(_Err(503)) is True          # PROVIDER_UNAVAILABLE
    assert _is_retryable(_Err(401)) is False         # AUTH
    assert _is_retryable(_Err(400)) is False         # INPUT_INVALID
    assert _is_retryable(TimeoutError()) is True     # transient I/O
    assert _is_retryable(asyncio.CancelledError()) is False  # control flow, never retried


class _Agent:
    """ainvoke raises `exc` for the first `fail_times` calls, then returns 'ok'."""
    def __init__(self, exc, fail_times):
        self.exc, self.fail_times, self.calls = exc, fail_times, 0

    async def ainvoke(self, messages, config):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return "ok"


@pytest.mark.asyncio
async def test_retries_then_succeeds_on_retryable(monkeypatch):
    # Skip the backoff wait so the test is instant.
    monkeypatch.setattr("app.llm.invoke_with_retry.retry.wait", lambda *a, **k: 0)
    agent = _Agent(_Err(429), fail_times=2)
    assert await invoke_with_retry(agent, {}, {}) == "ok"
    assert agent.calls == 3  # 2 failures + 1 success


@pytest.mark.asyncio
async def test_no_retry_on_auth():
    agent = _Agent(_Err(401), fail_times=1)
    with pytest.raises(_Err):
        await invoke_with_retry(agent, {}, {})
    assert agent.calls == 1  # failed fast, no retry


# --- Circuit breaker ---

def test_breaker_opens_and_cools_down(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.llm.time.monotonic", lambda: clock[0])
    br = _Breaker(threshold=2, cooldown_s=30)
    assert br.allow() is True
    br.record_failure()
    assert br.allow() is True          # 1 < threshold
    br.record_failure()
    assert br.allow() is False         # opened
    clock[0] += 31                     # past cooldown
    assert br.allow() is True          # half-open trial allowed
    br.record_success()                # trial succeeded → closed
    assert br.failures == 0 and br.allow() is True


@pytest.mark.asyncio
async def test_breaker_short_circuits_when_open(monkeypatch):
    monkeypatch.setattr("app.llm.invoke_with_retry.retry.wait", lambda *a, **k: 0)
    _breakers.clear()
    key = "test:endpoint"
    # threshold=1 so a single exhausted infra failure opens it.
    monkeypatch.setattr("app.llm.get_settings", lambda: _S(threshold=1, cooldown=30))
    agent = _Agent(_Err(503), fail_times=99)  # provider hard-down
    with pytest.raises(_Err):                  # first call exhausts retries → trips breaker
        await invoke_with_breaker(agent, {}, {}, breaker_key=key)
    before = agent.calls
    with pytest.raises(ProviderCircuitOpen):   # breaker open → no LLM call at all
        await invoke_with_breaker(agent, {}, {}, breaker_key=key)
    assert agent.calls == before               # provider not hit while open
    _breakers.clear()


class _S:
    def __init__(self, threshold, cooldown):
        self.llm_breaker_threshold = threshold
        self.llm_breaker_cooldown_s = cooldown
