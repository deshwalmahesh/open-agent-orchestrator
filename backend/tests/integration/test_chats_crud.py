"""Chat CRUD — cross-ref ownership validated at create time; no PATCH."""
from __future__ import annotations

from uuid import uuid4


def _create_agent(client, token: str, auth_header, sample_agent_config, name: str = "Researcher") -> str:
    r = client.post("/agents", json=sample_agent_config(name), headers=auth_header(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_create_chat_with_agent_only(client, signup_and_login, auth_header, sample_agent_config):
    token = signup_and_login()
    agent_id = _create_agent(client, token, auth_header, sample_agent_config)
    r = client.post("/chats", json={"agent_id": agent_id, "title": "First chat"}, headers=auth_header(token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["agent_id"] == agent_id
    assert body["persona_id"] is None
    assert body["title"] == "First chat"
    assert body["channel"] == "web"


def test_create_chat_with_own_persona(client, signup_and_login, auth_header, sample_agent_config):
    token = signup_and_login()
    h = auth_header(token)
    agent_id = _create_agent(client, token, auth_header, sample_agent_config)
    persona = client.post("/personas", json={"name": "Brief", "system_prompt": "Be brief."}, headers=h).json()
    r = client.post("/chats", json={"agent_id": agent_id, "persona_id": persona["id"]}, headers=h)
    assert r.status_code == 201
    assert r.json()["persona_id"] == persona["id"]


def test_create_chat_with_global_persona_ok(
    client, signup_and_login, auth_header, sample_agent_config, insert_global_persona
):
    global_pid = insert_global_persona()
    token = signup_and_login()
    agent_id = _create_agent(client, token, auth_header, sample_agent_config)
    r = client.post(
        "/chats", json={"agent_id": agent_id, "persona_id": global_pid}, headers=auth_header(token)
    )
    assert r.status_code == 201
    assert r.json()["persona_id"] == global_pid


def test_create_chat_404_when_agent_not_mine(client, signup_and_login, auth_header, sample_agent_config):
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    agent_id = _create_agent(client, alice, auth_header, sample_agent_config)
    # Bob can't attach Alice's agent to his chat.
    assert client.post("/chats", json={"agent_id": agent_id}, headers=auth_header(bob)).status_code == 404


def test_create_chat_404_when_persona_not_mine(client, signup_and_login, auth_header, sample_agent_config):
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    alice_persona = client.post(
        "/personas", json={"name": "Mine", "system_prompt": "x"}, headers=auth_header(alice)
    ).json()
    bob_agent = _create_agent(client, bob, auth_header, sample_agent_config)
    r = client.post(
        "/chats",
        json={"agent_id": bob_agent, "persona_id": alice_persona["id"]},
        headers=auth_header(bob),
    )
    assert r.status_code == 404


def test_create_chat_404_when_agent_does_not_exist(client, signup_and_login, auth_header):
    token = signup_and_login()
    r = client.post("/chats", json={"agent_id": str(uuid4())}, headers=auth_header(token))
    assert r.status_code == 404


def test_list_isolation_between_users(client, signup_and_login, auth_header, sample_agent_config):
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    alice_agent = _create_agent(client, alice, auth_header, sample_agent_config)
    bob_agent = _create_agent(client, bob, auth_header, sample_agent_config)
    client.post("/chats", json={"agent_id": alice_agent, "title": "A1"}, headers=auth_header(alice))
    client.post("/chats", json={"agent_id": alice_agent, "title": "A2"}, headers=auth_header(alice))
    client.post("/chats", json={"agent_id": bob_agent, "title": "B1"}, headers=auth_header(bob))

    assert {ch["title"] for ch in client.get("/chats", headers=auth_header(alice)).json()} == {"A1", "A2"}
    assert {ch["title"] for ch in client.get("/chats", headers=auth_header(bob)).json()} == {"B1"}


def test_get_one_404_cross_user(client, signup_and_login, auth_header, sample_agent_config):
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    agent_id = _create_agent(client, alice, auth_header, sample_agent_config)
    chat_id = client.post("/chats", json={"agent_id": agent_id}, headers=auth_header(alice)).json()["id"]
    assert client.get(f"/chats/{chat_id}", headers=auth_header(bob)).status_code == 404


def test_delete_chat_removes_it(client, signup_and_login, auth_header, sample_agent_config):
    token = signup_and_login()
    h = auth_header(token)
    agent_id = _create_agent(client, token, auth_header, sample_agent_config)
    chat_id = client.post("/chats", json={"agent_id": agent_id}, headers=h).json()["id"]

    assert client.delete(f"/chats/{chat_id}", headers=h).status_code == 204
    assert client.get(f"/chats/{chat_id}", headers=h).status_code == 404


def test_delete_chat_404_cross_user(client, signup_and_login, auth_header, sample_agent_config):
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    agent_id = _create_agent(client, alice, auth_header, sample_agent_config)
    chat_id = client.post("/chats", json={"agent_id": agent_id}, headers=auth_header(alice)).json()["id"]
    assert client.delete(f"/chats/{chat_id}", headers=auth_header(bob)).status_code == 404


def test_no_patch_endpoint(client, signup_and_login, auth_header, sample_agent_config):
    """Chats are immutable post-create in v1 — only create/list/get/delete."""
    token = signup_and_login()
    h = auth_header(token)
    agent_id = _create_agent(client, token, auth_header, sample_agent_config)
    chat_id = client.post("/chats", json={"agent_id": agent_id}, headers=h).json()["id"]
    assert client.patch(f"/chats/{chat_id}", json={"title": "x"}, headers=h).status_code == 405


def test_all_endpoints_require_auth(client):
    for method, path in [
        ("POST", "/chats"),
        ("GET", "/chats"),
        ("GET", "/chats/00000000-0000-0000-0000-000000000000"),
        ("DELETE", "/chats/00000000-0000-0000-0000-000000000000"),
    ]:
        r = client.request(method, path, json={"agent_id": str(uuid4())} if method == "POST" else None)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
