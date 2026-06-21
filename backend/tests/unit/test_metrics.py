"""Phase 5b: Prometheus metrics — /metrics endpoint + custom run/queue metrics."""
from __future__ import annotations

from prometheus_client import REGISTRY

from app import metrics


# --- custom domain metric: runs_total funnel ---

def test_record_run_increments_counter():
    def val(status, code):
        return REGISTRY.get_sample_value(
            "runs_total", {"status": status, "error_code": code}
        ) or 0.0

    # Deltas, not absolutes — other suite tests finalize runs into the same global counter.
    ok_before = val("succeeded", "none")
    metrics.record_run("succeeded", None)  # None → "none" label
    assert val("succeeded", "none") == ok_before + 1

    fail_before = val("failed", "RATE_LIMITED")
    metrics.record_run("failed", "RATE_LIMITED")
    assert val("failed", "RATE_LIMITED") == fail_before + 1


# --- /metrics endpoint is exposed and renders Prometheus text ---

def test_metrics_endpoint_exposed(client):
    client.get("/health")  # generate at least one HTTP sample
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.text
    assert "# HELP" in body and "# TYPE" in body
    assert "http_request" in body          # auto RED metrics from the instrumentator
    assert "runs_total" in body            # our custom counter is registered


# --- queue-depth endpoint mirrors into the gauge ---

def test_queue_depth_sets_gauge(client):
    r = client.get("/metrics/queue-depth")
    assert r.status_code == 200
    depth = r.json()["depth"]
    assert REGISTRY.get_sample_value("queue_depth") == float(depth)
