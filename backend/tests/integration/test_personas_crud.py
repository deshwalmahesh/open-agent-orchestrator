"""Persona CRUD — owner-can-edit, globals-read-only, cross-user isolation."""
from __future__ import annotations


def test_create_returns_201_with_owner(client, signup_and_login, auth_header):
    token = signup_and_login()
    r = client.post(
        "/personas", json={"name": "Friendly", "system_prompt": "Be warm."}, headers=auth_header(token)
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Friendly"
    assert body["system_prompt"] == "Be warm."
    assert body["owner_id"] is not None


def test_list_isolation_between_users_plus_shared_globals(
    client, signup_and_login, auth_header, insert_global_persona
):
    """Alice and Bob each see their own + the global; never each other's owned rows."""
    global_id = insert_global_persona("Concise", "Brief.")

    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    client.post("/personas", json={"name": "AliceVoice", "system_prompt": "..."}, headers=auth_header(alice))
    client.post("/personas", json={"name": "BobVoice", "system_prompt": "..."}, headers=auth_header(bob))

    # "Default Supervisor" is seeded by lifespan; both users always see it alongside owned + test globals.
    assert {p["name"] for p in client.get("/personas", headers=auth_header(alice)).json()} == {"AliceVoice", "Concise", "Default Supervisor"}
    assert {p["name"] for p in client.get("/personas", headers=auth_header(bob)).json()} == {"BobVoice", "Concise", "Default Supervisor"}

    # Globals come first per repo ordering, then alphabetically — "Concise" < "Default Supervisor".
    first = client.get("/personas", headers=auth_header(alice)).json()[0]
    assert first["id"] == global_id
    assert first["owner_id"] is None


def test_global_persona_not_editable(client, signup_and_login, auth_header, insert_global_persona):
    global_id = insert_global_persona()
    token = signup_and_login()
    r = client.put(
        f"/personas/{global_id}",
        json={"name": "Hacked", "system_prompt": "..."},
        headers=auth_header(token),
    )
    assert r.status_code == 404


def test_global_persona_not_deletable(client, signup_and_login, auth_header, insert_global_persona):
    global_id = insert_global_persona()
    token = signup_and_login()
    assert client.delete(f"/personas/{global_id}", headers=auth_header(token)).status_code == 404


def test_global_persona_readable_via_get_one(
    client, signup_and_login, auth_header, insert_global_persona
):
    global_id = insert_global_persona()
    token = signup_and_login()
    r = client.get(f"/personas/{global_id}", headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["owner_id"] is None


def test_cross_user_owned_persona_returns_404(client, signup_and_login, auth_header):
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    pid = client.post(
        "/personas", json={"name": "Secret", "system_prompt": "..."}, headers=auth_header(alice)
    ).json()["id"]

    assert client.get(f"/personas/{pid}", headers=auth_header(bob)).status_code == 404


def test_update_persists_for_owner(client, signup_and_login, auth_header):
    token = signup_and_login()
    h = auth_header(token)
    pid = client.post("/personas", json={"name": "V1", "system_prompt": "old"}, headers=h).json()["id"]

    r = client.put(f"/personas/{pid}", json={"name": "V2", "system_prompt": "new"}, headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "V2"
    assert client.get(f"/personas/{pid}", headers=h).json()["system_prompt"] == "new"


def test_all_endpoints_require_auth(client):
    for method, path in [
        ("POST", "/personas"),
        ("GET", "/personas"),
        ("GET", "/personas/00000000-0000-0000-0000-000000000000"),
        ("PUT", "/personas/00000000-0000-0000-0000-000000000000"),
        ("DELETE", "/personas/00000000-0000-0000-0000-000000000000"),
    ]:
        r = client.request(
            method, path, json={"name": "x", "system_prompt": "x"} if method in ("POST", "PUT") else None
        )
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_empty_name_or_prompt_rejected_422(client, signup_and_login, auth_header):
    token = signup_and_login()
    for bad in [{"name": "", "system_prompt": "x"}, {"name": "x", "system_prompt": ""}]:
        r = client.post("/personas", json=bad, headers=auth_header(token))
        assert r.status_code == 422
