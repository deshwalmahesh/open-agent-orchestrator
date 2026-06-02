def test_health_returns_ok(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_tools_returns_list(client):
    r = client.get("/tools")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    assert "calculator" in names
