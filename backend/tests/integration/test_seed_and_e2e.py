"""Step A6c — startup seed is idempotent + visible; first-user happy path end-to-end."""
from __future__ import annotations


def test_seeded_templates_visible_to_new_user(client, signup_and_login, auth_header):
    """Lifespan seeds 2 templates (research-and-write, supervised-loop) — every user sees them."""
    token = signup_and_login()
    rows = client.get("/workflows/templates", headers=auth_header(token)).json()
    names = {w["name"] for w in rows}
    assert "research-and-write" in names
    assert "supervised-loop" in names
    assert all(w["is_template"] is True for w in rows if w["name"] in names)


def test_seed_is_idempotent_across_test_clients(signup_and_login, auth_header):
    """Spin up a SECOND TestClient (same isolated DB) — seed must not duplicate."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    # First client triggers seed.
    with TestClient(create_app()) as c1:
        t1 = c1.post(
            "/auth/register",
            json={"email": "a@x.com", "password": "longenoughpwd123", "name": "A"},
        )
        assert t1.status_code == 201

    # Second client over the same DB — lifespan runs again; seed must skip existing rows.
    with TestClient(create_app()) as c2:
        login = c2.post(
            "/auth/jwt/login", data={"username": "a@x.com", "password": "longenoughpwd123"}
        )
        rows = c2.get(
            "/workflows/templates", headers={"Authorization": f"Bearer {login.json()['access_token']}"}
        ).json()
        names = [w["name"] for w in rows]
        assert names.count("research-and-write") == 1
        assert names.count("supervised-loop") == 1


def test_first_user_happy_path(client, signup_and_login, auth_header, sample_agent_config):
    """signup → templates visible → create persona → create agent → create chat with persona → list mine."""
    token = signup_and_login()
    h = auth_header(token)

    # Templates are immediately usable (read-only) without any clone-on-register.
    tmpls = client.get("/workflows/templates", headers=h).json()
    assert len(tmpls) >= 2

    # Personas: empty by default (we cut starter personas). User creates their own.
    assert client.get("/personas", headers=h).json() == []
    persona = client.post(
        "/personas", json={"name": "Brief", "system_prompt": "Be brief."}, headers=h
    ).json()

    # Agent.
    agent = client.post("/agents", json=sample_agent_config(), headers=h).json()

    # Chat with persona attached.
    chat = client.post(
        "/chats",
        json={"agent_id": agent["id"], "persona_id": persona["id"], "title": "First chat"},
        headers=h,
    )
    assert chat.status_code == 201, chat.text
    assert chat.json()["persona_id"] == persona["id"]

    # List mine.
    chats = client.get("/chats", headers=h).json()
    assert len(chats) == 1 and chats[0]["title"] == "First chat"
