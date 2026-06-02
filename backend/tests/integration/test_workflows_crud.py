"""Workflow CRUD — owner-can-edit, templates-read-only, cross-user isolation."""
from __future__ import annotations

import asyncio

from app.db import get_session_factory
from app.db.models import WorkflowDB


def _sample_workflow(name: str = "Research") -> dict:
    """Minimal valid WorkflowDef payload."""
    return {
        "name": name,
        "description": "demo",
        "entry": "start",
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "end", "type": "end"},
        ],
        "edges": [{"id": "e1", "source": "start", "target": "end"}],
        "is_template": False,
    }


def _insert_template(name: str = "Tmpl") -> str:
    """Insert a workflow template (user_id=NULL, is_template=True) directly."""
    async def _go() -> str:
        async with get_session_factory()() as s:
            row = WorkflowDB(user_id=None, name=name, definition=_sample_workflow(name), is_template=True)
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return str(row.id)
    return asyncio.run(_go())


def test_create_workflow_returns_201_owned_not_template(client, signup_and_login, auth_header):
    token = signup_and_login()
    r = client.post("/workflows", json=_sample_workflow(), headers=auth_header(token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Research"
    assert body["is_template"] is False
    assert body["owner_id"] is not None


def test_list_includes_templates_plus_owned(client, signup_and_login, auth_header):
    tmpl_id = _insert_template("ResearchAndWrite")
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    client.post("/workflows", json=_sample_workflow("AliceFlow"), headers=auth_header(alice))
    client.post("/workflows", json=_sample_workflow("BobFlow"), headers=auth_header(bob))

    alice_names = {w["name"] for w in client.get("/workflows", headers=auth_header(alice)).json()}
    bob_names = {w["name"] for w in client.get("/workflows", headers=auth_header(bob)).json()}

    assert "ResearchAndWrite" in alice_names and "ResearchAndWrite" in bob_names
    assert "AliceFlow" in alice_names and "AliceFlow" not in bob_names
    assert "BobFlow" in bob_names and "BobFlow" not in alice_names

    # Template should appear first per repo ordering.
    first = client.get("/workflows", headers=auth_header(alice)).json()[0]
    assert first["id"] == tmpl_id
    assert first["is_template"] is True


def test_templates_endpoint_lists_only_templates(client, signup_and_login, auth_header):
    tmpl_id = _insert_template("T1")
    token = signup_and_login()
    client.post("/workflows", json=_sample_workflow("Mine"), headers=auth_header(token))

    r = client.get("/workflows/templates", headers=auth_header(token))
    assert r.status_code == 200
    rows = r.json()
    # Startup seed adds 2 templates (research-and-write, supervised-loop); test adds 1 more.
    assert tmpl_id in {w["id"] for w in rows}
    assert all(w["is_template"] for w in rows)
    assert "Mine" not in {w["name"] for w in rows}


def test_template_not_editable(client, signup_and_login, auth_header):
    tmpl_id = _insert_template()
    token = signup_and_login()
    assert client.put(f"/workflows/{tmpl_id}", json=_sample_workflow("Hacked"), headers=auth_header(token)).status_code == 404


def test_template_not_deletable(client, signup_and_login, auth_header):
    tmpl_id = _insert_template()
    token = signup_and_login()
    assert client.delete(f"/workflows/{tmpl_id}", headers=auth_header(token)).status_code == 404


def test_template_readable_via_get_one(client, signup_and_login, auth_header):
    tmpl_id = _insert_template()
    token = signup_and_login()
    r = client.get(f"/workflows/{tmpl_id}", headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["is_template"] is True


def test_update_owned_persists(client, signup_and_login, auth_header):
    token = signup_and_login()
    h = auth_header(token)
    wid = client.post("/workflows", json=_sample_workflow(), headers=h).json()["id"]

    updated = _sample_workflow("Renamed")
    updated["description"] = "updated"
    r = client.put(f"/workflows/{wid}", json=updated, headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"
    assert client.get(f"/workflows/{wid}", headers=h).json()["definition"]["description"] == "updated"


def test_cross_user_owned_workflow_returns_404(client, signup_and_login, auth_header):
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    wid = client.post("/workflows", json=_sample_workflow(), headers=auth_header(alice)).json()["id"]

    assert client.get(f"/workflows/{wid}", headers=auth_header(bob)).status_code == 404
    assert client.put(f"/workflows/{wid}", json=_sample_workflow(), headers=auth_header(bob)).status_code == 404
    assert client.delete(f"/workflows/{wid}", headers=auth_header(bob)).status_code == 404


def test_all_endpoints_require_auth(client):
    for method, path in [
        ("POST", "/workflows"),
        ("GET", "/workflows"),
        ("GET", "/workflows/templates"),
        ("GET", "/workflows/00000000-0000-0000-0000-000000000000"),
        ("PUT", "/workflows/00000000-0000-0000-0000-000000000000"),
        ("DELETE", "/workflows/00000000-0000-0000-0000-000000000000"),
    ]:
        r = client.request(method, path, json=_sample_workflow() if method in ("POST", "PUT") else None)
        assert r.status_code == 401, f"{method} {path} -> {r.status_code}"


def test_invalid_workflow_definition_rejected_422(client, signup_and_login, auth_header):
    token = signup_and_login()
    bad = {"name": "X", "description": "x", "nodes": [], "edges": []}  # missing `entry`
    r = client.post("/workflows", json=bad, headers=auth_header(token))
    assert r.status_code == 422
