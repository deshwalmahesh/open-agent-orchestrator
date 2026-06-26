"""Smoke tests: HIL wiring is present after app boot — no LLM, no Redis required."""

from app.main import create_app
from app.runtime.tools import DISPLAY_NAMES, REGISTRY


def test_resume_route_mounted():
    app = create_app()
    paths = {r.path for r in app.routes}
    assert "/runs/{run_id}/resume" in paths


def test_ask_human_registered():
    assert "ask_human" in REGISTRY
    assert REGISTRY["ask_human"].name == "ask_human"
    assert DISPLAY_NAMES["ask_human"] == "Ask Human"


def test_worker_registers_resume_job():
    import app.worker as w

    names = {f.__name__ for f in w.WorkerSettings.functions}
    assert {"execute_run", "resume_run_job"} <= names
