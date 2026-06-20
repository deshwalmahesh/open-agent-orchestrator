"""Centralized run-failure taxonomy.

Every run failure maps to a stable `error_code`, a user-facing message, and a
retry policy. This is the single source of truth — `_execute`, the worker's
failure hook, and the channel adapters all go through `classify()` so the
failure UX never drifts between surfaces.

Design: we inspect the exception's HTTP status code and class name rather than
importing every provider SDK (openai / anthropic / google). That keeps this
module decoupled from provider packages and robust across providers whose
exception types we don't import.
"""
from __future__ import annotations

from dataclasses import dataclass

# Stable machine codes — persisted on RunDB.error_code and emitted in events.
QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"
INTERNAL = "INTERNAL"
INPUT_INVALID = "INPUT_INVALID"
AUTH = "AUTH"
RATE_LIMITED = "RATE_LIMITED"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
STEP_LIMIT = "STEP_LIMIT"
QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
INTERRUPTED = "INTERRUPTED"
RUN_TIMEOUT = "RUN_TIMEOUT"


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    user_message: str
    retryable: bool


# One canonical (message, retryable) per code. Centralized so every channel
# shows the same thing for the same failure.
_INFO: dict[str, tuple[str, bool]] = {
    QUEUE_UNAVAILABLE: ("Service is busy — please try again in a moment.", True),
    INTERNAL: ("Something went wrong on our side — we've logged it.", False),
    INPUT_INVALID: ("Your request couldn't be processed — try shortening or rephrasing it.", False),
    AUTH: ("Your model credentials are invalid or expired — update them in Settings.", False),
    RATE_LIMITED: ("The model is busy right now — please try again shortly.", True),
    PROVIDER_UNAVAILABLE: ("The model provider is temporarily unavailable — please try again.", True),
    STEP_LIMIT: ("I couldn't finish within the step budget — simplify or split the request.", False),
    QUOTA_EXCEEDED: ("You've reached your plan's usage limit.", False),
    INTERRUPTED: ("That run was interrupted — please resend your message.", False),
    RUN_TIMEOUT: ("That took too long and was stopped — try a simpler request.", False),
}


def info_for(code: str) -> ErrorInfo:
    """Look up the canonical message/retryable for a known code (default INTERNAL)."""
    msg, retryable = _INFO.get(code, _INFO[INTERNAL])
    return ErrorInfo(code=code, user_message=msg, retryable=retryable)


def _status_code(exc: BaseException) -> int | None:
    """HTTP status from an SDK exception (direct attr or nested under .response)."""
    val = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return val if isinstance(val, int) else None


def classify(exc: BaseException) -> ErrorInfo:
    """Map any run-execution exception to a stable ErrorInfo.

    NOTE: callers MUST re-raise asyncio.CancelledError BEFORE calling this —
    cancellation is control flow (shutdown / timeout), not a run failure.
    """
    name = type(exc).__name__.lower()

    # 1) LangGraph step-budget — checked by name to avoid importing langgraph here.
    if "recursion" in name:
        return info_for(STEP_LIMIT)

    # 2) Authoritative HTTP status, when the SDK exposes one.
    status = _status_code(exc)
    if status is not None:
        if status in (401, 403):
            return info_for(AUTH)
        if status == 429:
            return info_for(RATE_LIMITED)
        if status in (408, 409, 425) or status >= 500:  # transient 4xx + all 5xx
            return info_for(PROVIDER_UNAVAILABLE)
        if 400 <= status < 500:
            return info_for(INPUT_INVALID)

    # 3) Class-name heuristics for SDKs that don't surface a status code.
    if "ratelimit" in name:
        return info_for(RATE_LIMITED)
    if any(k in name for k in ("authentic", "permission", "apikey", "unauthorized")):
        return info_for(AUTH)
    if any(k in name for k in ("badrequest", "invalidrequest", "unprocessable", "validation", "notfound")):
        return info_for(INPUT_INVALID)
    if any(k in name for k in ("timeout", "connection", "internalserver", "overloaded",
                               "serviceunavailable", "servererror", "apierror", "apistatus")):
        return info_for(PROVIDER_UNAVAILABLE)

    # 4) Builtin transient I/O errors.
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return info_for(PROVIDER_UNAVAILABLE)

    # 5) Unknown — treat as our bug. Not auto-retried (could have side effects);
    # genuine process crashes are handled by idempotency + the startup reconciler.
    return info_for(INTERNAL)
