"""arq worker entrypoint — durable run execution off the API process.

Run with:  arq app.worker.WorkerSettings   (RUN_EXECUTOR=queue on the API side)

Same Docker image as the API, different command. The worker owns the LangGraph
Redis checkpointer; the API only enqueues. arq uses pessimistic execution — a job
killed mid-run is re-queued and re-run, which is safe because `_execute` is
idempotent (see run_service).
"""
from __future__ import annotations

from uuid import UUID

import structlog
from arq.connections import RedisSettings

from app.config import get_settings
from app.logging import configure_logging
from app.runtime.checkpointer import build_checkpointer
from app.services.run_service import _execute, resume_run, set_checkpointer

log = structlog.get_logger()


async def execute_run(
    ctx: dict, run_id: str, chat_id: str, user_text: str, files: list[dict],
    request_id: str | None = None,
) -> None:
    """arq task: thin shim over the existing _execute. Args are JSON-simple
    (str ids + list) so they serialize cleanly onto the queue. The request_id
    from the originating HTTP call is re-bound so worker logs correlate."""
    structlog.contextvars.bind_contextvars(request_id=request_id or "-")
    try:
        await _execute(UUID(run_id), UUID(chat_id), user_text, files)
    finally:
        structlog.contextvars.clear_contextvars()


async def resume_run_job(
    ctx: dict, run_id: str, decisions: list[dict], request_id: str | None = None
) -> None:
    """arq task: resume a run paused on a human-in-the-loop interrupt. Idempotent — a
    redelivery after the run already resumed/finished no-ops (mark_run_resumed guard)."""
    structlog.contextvars.bind_contextvars(request_id=request_id or "-")
    try:
        await resume_run(UUID(run_id), decisions)
    finally:
        structlog.contextvars.clear_contextvars()


async def startup(ctx: dict) -> None:
    s = get_settings()
    configure_logging(s.log_level)
    log.info("worker.startup", max_jobs=s.worker_max_jobs, job_timeout=s.run_timeout_s)
    # Expose Prometheus /metrics from this non-HTTP process so runs_total (incremented
    # here in queue mode) is scrapable on worker pods. Best-effort.
    from app.metrics import start_worker_metrics_server
    start_worker_metrics_server(s.worker_metrics_port)
    try:
        saver, client = await build_checkpointer()
        set_checkpointer(saver)
        ctx["redis_client"] = client
    except Exception as exc:
        # Runs still execute without the checkpointer (no within-run graph replay).
        log.warning("worker.checkpointer_unavailable", error=str(exc))
        ctx["redis_client"] = None


async def shutdown(ctx: dict) -> None:
    client = ctx.get("redis_client")
    if client is not None:
        await client.aclose()
    log.info("worker.shutdown")


class WorkerSettings:
    """Read by `arq app.worker.WorkerSettings`."""

    functions = [execute_run, resume_run_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = get_settings().worker_max_jobs   # global concurrency cap = backpressure
    job_timeout = get_settings().run_timeout_s  # per-run wall-clock cap
    max_tries = get_settings().run_max_tries    # bounds crash redelivery
