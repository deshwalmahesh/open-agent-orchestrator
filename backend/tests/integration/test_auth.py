"""Real HTTP integration tests for fastapi-users JWT auth — no mocks below TestClient."""
from __future__ import annotations


def test_signup_returns_user_with_extra_fields(client):
    r = client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "longenoughpwd123", "name": "Alice"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "user@example.com"
    assert body["name"] == "Alice"
    assert body["slack_user_id"] is None
    assert body["is_active"] is True
    # Password is never echoed back.
    assert "password" not in body
    assert "hashed_password" not in body


def test_duplicate_signup_rejected(client, signup_and_login):
    signup_and_login()
    r = client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "anotherpassword", "name": "Bob"},
    )
    assert r.status_code == 400, r.text


def test_login_returns_bearer_token(signup_and_login):
    token = signup_and_login()
    assert token and isinstance(token, str)


def test_login_wrong_password_rejected(client, signup_and_login):
    signup_and_login()
    r = client.post(
        "/auth/jwt/login", data={"username": "alice@example.com", "password": "wrong-password"}
    )
    assert r.status_code == 400, r.text


def test_me_with_token_returns_self(client, signup_and_login, auth_header):
    token = signup_and_login()
    r = client.get("/users/me", headers=auth_header(token))
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "alice@example.com"


def test_me_without_token_unauthorized(client):
    assert client.get("/users/me").status_code == 401


def test_me_with_garbage_token_unauthorized(client, auth_header):
    assert client.get("/users/me", headers=auth_header("not-a-real-jwt")).status_code == 401


def test_user_update_persists(client, signup_and_login, auth_header):
    token = signup_and_login()
    h = auth_header(token)
    r = client.patch("/users/me", headers=h, json={"name": "Renamed", "slack_user_id": "U123"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Renamed"
    assert r.json()["slack_user_id"] == "U123"

    # Re-fetch to confirm persistence (not just echoed back).
    r2 = client.get("/users/me", headers=h)
    assert r2.json()["name"] == "Renamed"
    assert r2.json()["slack_user_id"] == "U123"
