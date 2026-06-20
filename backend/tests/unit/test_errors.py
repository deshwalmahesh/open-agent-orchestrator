"""Unit tests for the run-failure taxonomy (app.errors.classify)."""
from __future__ import annotations

import pytest

from app import errors
from app.errors import classify


class _StatusExc(Exception):
    """Mimics SDK exceptions that carry an HTTP status_code."""
    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


class _RespExc(Exception):
    """Mimics SDK exceptions that nest status under .response.status_code."""
    def __init__(self, status_code: int) -> None:
        super().__init__("resp error")
        self.response = type("R", (), {"status_code": status_code})()


# Name-only exceptions (no status code) — exercise the heuristic branch.
class RateLimitError(Exception): ...
class AuthenticationError(Exception): ...
class BadRequestError(Exception): ...
class APITimeoutError(Exception): ...
class InternalServerError(Exception): ...
class GraphRecursionError(Exception): ...


@pytest.mark.parametrize("status,expected", [
    (401, errors.AUTH),
    (403, errors.AUTH),
    (429, errors.RATE_LIMITED),
    (400, errors.INPUT_INVALID),
    (404, errors.INPUT_INVALID),
    (500, errors.PROVIDER_UNAVAILABLE),
    (503, errors.PROVIDER_UNAVAILABLE),
    (529, errors.PROVIDER_UNAVAILABLE),  # Anthropic "overloaded"
])
def test_status_code_mapping(status, expected):
    assert classify(_StatusExc(status)).code == expected
    assert classify(_RespExc(status)).code == expected


@pytest.mark.parametrize("exc,expected", [
    (RateLimitError(), errors.RATE_LIMITED),
    (AuthenticationError(), errors.AUTH),
    (BadRequestError(), errors.INPUT_INVALID),
    (APITimeoutError(), errors.PROVIDER_UNAVAILABLE),
    (InternalServerError(), errors.PROVIDER_UNAVAILABLE),
    (GraphRecursionError(), errors.STEP_LIMIT),
    (TimeoutError(), errors.PROVIDER_UNAVAILABLE),
    (ConnectionError(), errors.PROVIDER_UNAVAILABLE),
    (OSError(), errors.PROVIDER_UNAVAILABLE),
    (ValueError("boom"), errors.INTERNAL),  # unknown → INTERNAL
])
def test_name_and_builtin_mapping(exc, expected):
    assert classify(exc).code == expected


def test_retryable_policy():
    # Transient classes retry; user-fault / terminal classes do not.
    assert classify(RateLimitError()).retryable is True
    assert classify(_StatusExc(503)).retryable is True
    assert classify(AuthenticationError()).retryable is False
    assert classify(BadRequestError()).retryable is False
    assert classify(GraphRecursionError()).retryable is False
    assert classify(ValueError()).retryable is False  # unknown not auto-retried


def test_every_code_has_a_message():
    for code in (errors.QUEUE_UNAVAILABLE, errors.INTERNAL, errors.INPUT_INVALID,
                 errors.AUTH, errors.RATE_LIMITED, errors.PROVIDER_UNAVAILABLE,
                 errors.STEP_LIMIT, errors.QUOTA_EXCEEDED, errors.INTERRUPTED,
                 errors.RUN_TIMEOUT):
        info = errors.info_for(code)
        assert info.code == code
        assert info.user_message  # non-empty
