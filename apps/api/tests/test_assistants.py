from __future__ import annotations

from httpx import AsyncClient


async def test_create_lists_and_gets_assistant(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/assistants", json={"name": "Support Bot"}, headers=org_headers
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["slug"] == "support-bot"
    assert body["status"] == "draft"
    # a fresh assistant compiles from a valid default graph
    assert body["draft_validation"]["errors"] == []
    assert body["draft_config"]["models"]["main"]["model"] == "sonnet"
    assert {n["type"] for n in body["draft_graph"]["nodes"]} == {
        "input",
        "guardrail",
        "memory",
        "agent",
        "output",
    }

    listing = await client.get("/api/v1/assistants", headers=org_headers)
    assert [a["slug"] for a in listing.json()["items"]] == ["support-bot"]

    got = await client.get(f"/api/v1/assistants/{body['id']}", headers=org_headers)
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


async def test_requires_org_header(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    resp = await client.get("/api/v1/assistants", headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "org_required"


async def test_save_draft_config_reprojects_graph(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    a = (await client.post("/api/v1/assistants", json={"name": "A"}, headers=org_headers)).json()
    cfg = a["draft_config"]
    cfg["system_prompt"] = "You are a pirate."
    cfg["tools"]["calculator"]["enabled"] = True

    resp = await client.put(
        f"/api/v1/assistants/{a['id']}/draft-config", json=cfg, headers=org_headers
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["config"]["system_prompt"] == "You are a pirate."
    # the calculator tool now shows up as a wired node
    tool_nodes = [n for n in out["graph"]["nodes"] if n["type"] == "tool"]
    assert [n["data"]["key"] for n in tool_nodes] == ["calculator"]


async def test_save_invalid_draft_graph_is_stored_but_not_compiled(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    a = (await client.post("/api/v1/assistants", json={"name": "B"}, headers=org_headers)).json()
    broken = a["draft_graph"]
    broken["nodes"] = [n for n in broken["nodes"] if n["type"] != "agent"]  # remove the agent

    resp = await client.put(
        f"/api/v1/assistants/{a['id']}/draft-graph", json=broken, headers=org_headers
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["config"] is None
    assert any(e["code"] == "missing_node" for e in out["validation"]["errors"])


async def test_publish_rejects_invalid_then_versions_and_diffs(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    a = (await client.post("/api/v1/assistants", json={"name": "C"}, headers=org_headers)).json()
    aid = a["id"]

    # publish v1 from the default draft
    v1 = await client.post(
        f"/api/v1/assistants/{aid}/versions", json={"note": "first"}, headers=org_headers
    )
    assert v1.status_code == 201, v1.text
    assert v1.json()["version_number"] == 1

    # tweak the config, publish v2
    cfg = a["draft_config"]
    cfg["models"]["main"]["model"] = "claude-opus-5"
    await client.put(f"/api/v1/assistants/{aid}/draft-config", json=cfg, headers=org_headers)
    v2 = await client.post(
        f"/api/v1/assistants/{aid}/versions", json={"note": "opus"}, headers=org_headers
    )
    assert v2.json()["version_number"] == 2

    versions = await client.get(f"/api/v1/assistants/{aid}/versions", headers=org_headers)
    assert [v["version_number"] for v in versions.json()["items"]] == [2, 1]

    diff = await client.get(f"/api/v1/assistants/{aid}/versions/diff?a=1&b=2", headers=org_headers)
    assert diff.status_code == 200
    paths = {d["path"] for d in diff.json()["config_diff"]}
    assert "models.main.model" in paths

    # break the draft (remove the agent node), publishing must fail
    broken = (await client.get(f"/api/v1/assistants/{aid}/draft-graph", headers=org_headers)).json()
    broken["nodes"] = [n for n in broken["nodes"] if n["type"] != "agent"]
    await client.put(f"/api/v1/assistants/{aid}/draft-graph", json=broken, headers=org_headers)
    bad = await client.post(f"/api/v1/assistants/{aid}/versions", json={}, headers=org_headers)
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "graph_invalid"


async def test_cross_tenant_assistant_is_404(client: AsyncClient) -> None:
    async def signup(email: str) -> dict[str, str]:
        r = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "supersecret", "name": "x"},
        )
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
        me = await client.get("/api/v1/auth/me", headers=h)
        return {**h, "X-Org-Id": me.json()["memberships"][0]["org_id"]}

    alice = await signup("alice@example.com")
    bob = await signup("bob@example.com")
    a = (await client.post("/api/v1/assistants", json={"name": "secret"}, headers=alice)).json()

    # bob uses his own org header but alice's assistant id
    resp = await client.get(f"/api/v1/assistants/{a['id']}", headers=bob)
    assert resp.status_code == 404


async def test_the_graph_diff_names_node_types(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """A version diff that says `db` was added is unreadable; it must say what
    kind of node that is. Ids are the canvas's own, so they survive publishes."""
    a = (await client.post("/api/v1/assistants", json={"name": "D"}, headers=org_headers)).json()
    aid = a["id"]
    await client.post(
        f"/api/v1/assistants/{aid}/versions", json={"note": "v1"}, headers=org_headers
    )
    cfg = a["draft_config"]
    cfg["tools"]["calculator"] = {"enabled": True}
    await client.put(f"/api/v1/assistants/{aid}/draft-config", json=cfg, headers=org_headers)
    await client.post(
        f"/api/v1/assistants/{aid}/versions", json={"note": "v2"}, headers=org_headers
    )

    diff = (
        await client.get(f"/api/v1/assistants/{aid}/versions/diff?a=1&b=2", headers=org_headers)
    ).json()["graph_diff"]
    (added,) = diff["nodes_added"]
    assert diff["node_types"][added] == "tool"
    assert [added, "agent"] in diff["edges_added"]
    assert diff["node_types"]["agent"] == "agent"
