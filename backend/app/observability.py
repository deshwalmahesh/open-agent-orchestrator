"""Langfuse integration — off-the-shelf LLM observability, env-gated and lazy.

We do NOT hand-roll tracing: the LangChain CallbackHandler captures tool calls
(including MCP tools — they're standard LangChain tools), token usage, latency, and
nested sub-agent spans automatically, via the same config={"callbacks":[...]} slot.

Disabled (every function is a no-op) unless LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY
are set, so dev/tests need nothing. Run traces use a deterministic trace id derived
from run_id so user feedback (thumbs) can be attached later as a Langfuse score.
"""
from __future__ import annotations

from contextlib import nullcontext
from uuid import UUID

import structlog

from app.config import get_settings

log = structlog.get_logger()


def enabled() -> bool:
    s = get_settings()
    return bool(s.langfuse_public_key and s.langfuse_secret_key)


def trace_id_for(run_id: UUID) -> str:
    """Deterministic Langfuse trace id for a run — same id at run time and at
    feedback time, so scores attach to the right trace."""
    from langfuse import Langfuse
    return Langfuse.create_trace_id(seed=str(run_id))


def get_handler():
    """LangChain CallbackHandler if Langfuse is configured, else None."""
    if not enabled():
        return None
    try:
        from langfuse.langchain import CallbackHandler
        return CallbackHandler()
    except Exception as exc:  # import/SDK error must never break a run
        log.warning("langfuse.handler_unavailable", error=str(exc))
        return None


def run_span(run_id: UUID):
    """Context manager pinning the run's trace to trace_id_for(run_id) so the handler's
    spans land on a known trace. Returns a nullcontext when Langfuse is disabled."""
    if not enabled():
        return nullcontext()
    try:
        from langfuse import get_client
        return get_client().start_as_current_observation(
            as_type="span", name="run", trace_context={"trace_id": trace_id_for(run_id)}
        )
    except Exception as exc:
        log.warning("langfuse.span_unavailable", error=str(exc))
        return nullcontext()


def record_score(run_id: UUID, *, name: str, value: int, comment: str | None = None) -> None:
    """Mirror a thumbs up/down to a Langfuse BOOLEAN score on the run's trace.
    Best-effort: the DB (FeedbackDB) is the source of truth; this is for analytics."""
    if not enabled():
        return
    try:
        from langfuse import get_client
        get_client().create_score(
            trace_id=trace_id_for(run_id), name=name, value=value,
            data_type="BOOLEAN", comment=comment,
        )
    except Exception as exc:
        log.warning("langfuse.score_failed", error=str(exc))
