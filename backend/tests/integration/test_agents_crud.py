"""Agent CRUD integration tests — real HTTP, real DB. Cross-user isolation enforced."""
from __future__ import annotations


def test_create_returns_201_with_config_echoed(client, signup_and_login, auth_header, sample_agent_config):
    token = signup_and_login()
    r = client.post("/agents", json=sample_agent_config(), headers=auth_header(token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Researcher"
    assert body["config"]["role"] == "researcher"
    assert body["config"]["llm"]["model"] == "qwen3-coder"
    assert "id" in body
    assert "created_at" in body


def test_list_returns_only_owner_agents(client, signup_and_login, auth_header, sample_agent_config):
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")

    client.post("/agents", json=sample_agent_config("AliceBot"), headers=auth_header(alice))
    client.post("/agents", json=sample_agent_config("AliceBot2"), headers=auth_header(alice))
    client.post("/agents", json=sample_agent_config("BobBot"), headers=auth_header(bob))

    assert {a["name"] for a in client.get("/agents", headers=auth_header(alice)).json()} == {"AliceBot", "AliceBot2"}
    assert {a["name"] for a in client.get("/agents", headers=auth_header(bob)).json()} == {"BobBot"}


def test_get_one_404_when_not_owner(client, signup_and_login, auth_header, sample_agent_config):
    """Cross-user access returns 404 (not 403) — we don't leak existence."""
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    agent_id = client.post("/agents", json=sample_agent_config(), headers=auth_header(alice)).json()["id"]

    assert client.get(f"/agents/{agent_id}", headers=auth_header(alice)).status_code == 200
    assert client.get(f"/agents/{agent_id}", headers=auth_header(bob)).status_code == 404


def test_update_persists(client, signup_and_login, auth_header, sample_agent_config):
    token = signup_and_login()
    h = auth_header(token)
    agent_id = client.post("/agents", json=sample_agent_config(), headers=h).json()["id"]

    updated = sample_agent_config("Renamed", system_prompt="Updated prompt.")
    r = client.put(f"/agents/{agent_id}", json=updated, headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"

    # Re-fetch to confirm persistence.
    r = client.get(f"/agents/{agent_id}", headers=h)
    assert r.json()["name"] == "Renamed"
    assert r.json()["config"]["system_prompt"] == "Updated prompt."


def test_delete_removes_agent(client, signup_and_login, auth_header, sample_agent_config):
    token = signup_and_login()
    h = auth_header(token)
    agent_id = client.post("/agents", json=sample_agent_config(), headers=h).json()["id"]

    assert client.delete(f"/agents/{agent_id}", headers=h).status_code == 204
    assert client.get(f"/agents/{agent_id}", headers=h).status_code == 404


def test_delete_404_when_not_owner(client, signup_and_login, auth_header, sample_agent_config):
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    agent_id = client.post("/agents", json=sample_agent_config(), headers=auth_header(alice)).json()["id"]

    assert client.delete(f"/agents/{agent_id}", headers=auth_header(bob)).status_code == 404
    # Confirm Alice's agent still exists.
    assert client.get(f"/agents/{agent_id}", headers=auth_header(alice)).status_code == 200


def test_all_endpoints_require_auth(client, sample_agent_config):
    for method, path in [
        ("POST", "/agents"),
        ("GET", "/agents"),
        ("GET", "/agents/00000000-0000-0000-0000-000000000000"),
        ("PUT", "/agents/00000000-0000-0000-0000-000000000000"),
        ("DELETE", "/agents/00000000-0000-0000-0000-000000000000"),
    ]:
        r = client.request(method, path, json=sample_agent_config() if method in ("POST", "PUT") else None)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_invalid_agent_config_rejected_422(client, signup_and_login, auth_header):
    token = signup_and_login()
    # Missing required `system_prompt` field.
    bad = {"name": "X", "role": "r", "llm": {"base_url": "x", "model": "m"}}
    r = client.post("/agents", json=bad, headers=auth_header(token))
    assert r.status_code == 422
