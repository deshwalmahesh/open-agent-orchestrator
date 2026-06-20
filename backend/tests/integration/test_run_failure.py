"""Integration: a run whose LLM call raises a classified error finishes as
`failed` with a stable error_code on the run row AND in the run.finished event.

No live creds — we stub run_service.invoke_with_retry to raise. This exercises
the real spine (API → start_run → background _execute → classify → finalize_run
→ event emit) minus the network round-trip.
"""
from __future__ import annotations

import json
import time


class RateLimitError(Exception):
    """Name maps to RATE_LIMITED via app.errors.classify heuristics."""


def _agent_payload() -> dict:
    return {
        "name": "Echo",
        "role": "assistant",
        "system_prompt": "Reply briefly.",
        "llm": {"provider": "openai", "base_url": "http://stub.local/v1",
                "api_key": "stub", "model": "stub-model"},
        "tools": [],
    }


def test_run_failure_surfaces_error_code(client, signup_and_login, auth_header, monkeypatch):
    async def _boom(agent, messages, config, *, breaker_key=None):
        raise RateLimitError("429 too many requests")

    # _execute looks up invoke_with_breaker as a module global at call time, so
    # patching the run_service binding takes effect for the background task.
    monkeypatch.setattr("app.services.run_service.invoke_with_breaker", _boom)

    token = signup_and_login()
    h = auth_header(token)
    agent = client.post("/agents", json=_agent_payload(), headers=h).json()
    assert client.post(f"/agents/{agent['id']}/deploy", headers=h).status_code == 200
    chat = client.post("/chats", json={"agent_id": agent["id"]}, headers=h).json()

    r = client.post(f"/chats/{chat['id']}/messages", json={"text": "hi"}, headers=h)
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]

    deadline = time.time() + 15
    body = None
    while time.time() < deadline:
        body = client.get(f"/runs/{run_id}", headers=h).json()
        if body["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.25)

    assert body["status"] == "failed", body
    assert body["error_code"] == "RATE_LIMITED", body
    assert body["error"]  # user-facing message persisted

    # The terminal event carries the same machine code for the SSE/UI surface.
    events = client.get(f"/runs/{run_id}/events", headers=h, params={"after_seq": 0}).text
    finished = [json.loads(line[len("data:"):].strip())
                for blk in events.split("\n\n") if "run.finished" in blk
                for line in blk.splitlines() if line.startswith("data:")]
    assert any(d.get("error_code") == "RATE_LIMITED" for d in finished), events
