"""Sub-agent tree validation: cycles, depth caps, cross-user, name collisions."""
from __future__ import annotations

from uuid import uuid4


def _create_agent(client, token, auth_header, sample_agent_config, name="A", **extra):
    r = client.post("/agents", json=sample_agent_config(name, **extra), headers=auth_header(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_cycle_rejected(client, signup_and_login, auth_header, sample_agent_config):
    """A -> B -> A must be rejected at PATCH time."""
    token = signup_and_login()
    h = auth_header(token)
    a_id = _create_agent(client, token, auth_header, sample_agent_config, "AgentA")
    b_id = _create_agent(client, token, auth_header, sample_agent_config, "AgentB", subagents=[a_id])

    # Now try to make A point at B (creating a cycle)
    cfg = sample_agent_config("AgentA", subagents=[b_id])
    r = client.put(f"/agents/{a_id}", json=cfg, headers=h)
    assert r.status_code == 400
    assert "cycle" in r.text.lower()


def test_depth_exceeded(client, signup_and_login, auth_header, sample_agent_config):
    """Chain of 5 agents exceeds MAX_AGENT_DEPTH=4."""
    token = signup_and_login()
    h = auth_header(token)

    # Build a chain: e -> d -> c -> b -> a (depth 5)
    a = _create_agent(client, token, auth_header, sample_agent_config, "L1")
    b = _create_agent(client, token, auth_header, sample_agent_config, "L2", subagents=[a])
    c = _create_agent(client, token, auth_header, sample_agent_config, "L3", subagents=[b])
    d = _create_agent(client, token, auth_header, sample_agent_config, "L4", subagents=[c])

    # Depth 5 = root + 4 levels → should be rejected
    cfg = sample_agent_config("L5", subagents=[d])
    r = client.post("/agents", json=cfg, headers=h)
    assert r.status_code == 400
    assert "depth" in r.text.lower()


def test_cross_user_subagent_rejected(client, signup_and_login, auth_header, sample_agent_config):
    """Alice owns A; Bob can't reference A in his sub-agents."""
    alice = signup_and_login("alice@example.com")
    bob = signup_and_login("bob@example.com")
    alice_agent = _create_agent(client, alice, auth_header, sample_agent_config, "AliceAgent")

    cfg = sample_agent_config("BobSupervisor", subagents=[alice_agent])
    r = client.post("/agents", json=cfg, headers=auth_header(bob))
    assert r.status_code == 400
    assert "not found" in r.text.lower()


def test_name_collision_with_registry(client, signup_and_login, auth_header, sample_agent_config):
    """Sub-agent whose name matches a built-in tool (e.g. 'calculator') must be rejected."""
    token = signup_and_login()
    h = auth_header(token)
    # Create agent named "calculator" — that's fine on its own
    calc_id = _create_agent(client, token, auth_header, sample_agent_config, "calculator")

    # But referencing it as a sub-agent collides with the registry tool
    cfg = sample_agent_config("Boss", subagents=[calc_id])
    r = client.post("/agents", json=cfg, headers=h)
    assert r.status_code == 400
    assert "collides" in r.text.lower()


def test_valid_2level_tree_accepted(client, signup_and_login, auth_header, sample_agent_config):
    """A valid supervisor with 2 sub-agents should succeed."""
    token = signup_and_login()
    h = auth_header(token)
    researcher = _create_agent(client, token, auth_header, sample_agent_config, "Researcher")
    writer = _create_agent(client, token, auth_header, sample_agent_config, "Writer")

    cfg = sample_agent_config("Boss", subagents=[researcher, writer])
    r = client.post("/agents", json=cfg, headers=h)
    assert r.status_code == 201
    assert len(r.json()["config"]["subagents"]) == 2


def test_nonexistent_subagent_rejected(client, signup_and_login, auth_header, sample_agent_config):
    """Referencing a UUID that doesn't exist in the DB must 400."""
    token = signup_and_login()
    cfg = sample_agent_config("Boss", subagents=[str(uuid4())])
    r = client.post("/agents", json=cfg, headers=auth_header(token))
    assert r.status_code == 400
    assert "not found" in r.text.lower()
