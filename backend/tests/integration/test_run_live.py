"""End-to-end: POST /chats/{id}/messages → background run → SSE stream → message persisted.

Live LLM. Skipped if no creds. This is the regression that catches the whole
spine (run_service + agent build + LLM round-trip + DB persistence + SSE drain).
"""
from __future__ import annotations

import time

import pytest

from app.config import get_settings

_s = get_settings()
_HAS_LLM = bool(_s.vllm_base_url and _s.vllm_api_key and _s.vllm_default_model)

pytestmark = pytest.mark.skipif(not _HAS_LLM, reason="no live LLM creds")


def _agent_payload() -> dict:
    return {
        "name": "Echo",
        "role": "assistant",
        "system_prompt": "Reply with one short sentence. No tools needed.",
        "llm": {
            "base_url": _s.vllm_base_url,
            "api_key": _s.vllm_api_key,
            "model": _s.vllm_default_model,
            "max_tokens": 1024,
            "timeout_s": 60.0,
        },
        "tools": [],
    }


def test_post_message_runs_and_persists(client, signup_and_login, auth_header):
    """Happy path: schedule run, poll for completion, verify message + run rows."""
    token = signup_and_login()
    h = auth_header(token)
    agent = client.post("/agents", json=_agent_payload(), headers=h).json()
    chat = client.post("/chats", json={"agent_id": agent["id"]}, headers=h).json()

    r = client.post(f"/chats/{chat['id']}/messages", json={"text": "Say hi."}, headers=h)
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]

    # Poll the run row until it finishes (test is live LLM so we accept ~60s).
    deadline = time.time() + 90
    status_val = None
    while time.time() < deadline:
        r = client.get(f"/runs/{run_id}", headers=h)
        assert r.status_code == 200
        status_val = r.json()["status"]
        if status_val in ("succeeded", "failed"):
            break
        time.sleep(0.5)
    if status_val != "succeeded":
        err = client.get(f"/runs/{run_id}", headers=h).json()
        pytest.fail(f"run did not succeed (status={status_val}, error={err.get('error')})")

    # Messages persisted: user turn + at least one agent reply row.
    # Content may be empty on flaky reasoning-model returns — we assert the row exists,
    # the run-finished event below is the stronger correctness signal.
    msgs = client.get(f"/chats/{chat['id']}/messages", headers=h).json()
    assert len(msgs) >= 2
    assert msgs[0]["sender"] == "user" and msgs[0]["content"] == "Say hi."
    assert msgs[-1]["sender"] != "user"

    # Backlog event replay (after_seq=0): at minimum run.started + run.finished.
    r = client.get(f"/runs/{run_id}/events", headers=h, params={"after_seq": 0})
    assert r.status_code == 200
    types = []
    for line in r.text.splitlines():
        if line.startswith("event:"):
            types.append(line.split(":", 1)[1].strip())
    assert "run.started" in types
    assert "run.finished" in types


def test_cross_user_run_returns_404(client, signup_and_login, auth_header):
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    agent = client.post("/agents", json=_agent_payload(), headers=auth_header(alice)).json()
    chat = client.post("/chats", json={"agent_id": agent["id"]}, headers=auth_header(alice)).json()
    run_id = client.post(
        f"/chats/{chat['id']}/messages", json={"text": "hi"}, headers=auth_header(alice)
    ).json()["run_id"]

    assert client.get(f"/runs/{run_id}", headers=auth_header(bob)).status_code == 404
    assert client.get(f"/runs/{run_id}/events", headers=auth_header(bob)).status_code == 404
