"""Prometheus metrics — app/infra observability (the open-source New-Relic replacement).

HTTP RED metrics (request rate / errors / duration→p95) come free from
prometheus-fastapi-instrumentator (one line in main.create_app, scraped at GET /metrics).
Here we add the domain signals that aren't auto-captured: run throughput by outcome,
and queue backlog. Extend by declaring another Counter/Gauge/Histogram below — that's
the whole pattern (and it's what makes this "little code, fully extendable").

All metrics use prometheus_client's default REGISTRY (what the instrumentator and the
worker's start_http_server both export). We run ONE process per pod (HPA scales pods),
so no PROMETHEUS_MULTIPROC_DIR is needed — Prometheus scrapes each pod directly.
Grafana scrapes /metrics for dashboards + alerting/SLOs; nothing here is vendor-locked.
"""
from __future__ import annotations

import structlog
from prometheus_client import Counter, Gauge

log = structlog.get_logger()

RUNS_TOTAL = Counter("runs_total", "Agent runs by terminal status.", ["status", "error_code"])
QUEUE_DEPTH = Gauge("queue_depth", "arq backlog (ZCARD of the run queue), set on each poll.")


def record_run(status: str, error_code: str | None) -> None:
    """One funnel for run outcomes — called from finalize_run (every terminal transition)."""
    RUNS_TOTAL.labels(status=status, error_code=error_code or "none").inc()


def start_worker_metrics_server(port: int) -> None:
    """Expose the default REGISTRY over HTTP from the (non-HTTP) arq worker, so Prometheus
    can scrape worker pods too. Best-effort: a metrics-server hiccup must not kill the worker."""
    try:
        from prometheus_client import start_http_server
        start_http_server(port)
        log.info("metrics.worker_server_started", port=port)
    except Exception as exc:  # noqa: BLE001
        log.warning("metrics.worker_server_failed", port=port, error=str(exc))
