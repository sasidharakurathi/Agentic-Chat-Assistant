"""A Panels save must not rearrange the canvas (audit: canvas node-id defect).

The build page mints short node ids ("db", "ds"); `PUT /draft-config`
re-projects the graph from the config. The projection used to re-derive its
own ids and look saved positions up by id, so the first Panels save renamed
those nodes and snapped them to default positions — and any edges the user
drew in an equivalent-but-different shape were rewired too.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_a_panels_save_keeps_canvas_ids_positions_and_wiring(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    a = (
        await client.post("/api/v1/assistants", json={"name": "Canvas"}, headers=org_headers)
    ).json()
    aid = a["id"]
    conn = await client.post(
        f"/api/v1/assistants/{aid}/db-connections",
        json={"name": "PG", "engine": "postgres", "host": "db", "database": "x"},
        headers=org_headers,
    )
    cid = conn.json()["id"]

    def node(nid: str, kind: str, x: float, data: dict[str, Any] | None = None) -> dict:
        return {"id": nid, "type": kind, "position": {"x": x, "y": x}, "data": data or {}}

    graph = {
        "schema_version": 1,
        "nodes": [
            node("input", "input", 1),
            node("agent", "agent", 2),
            node("output", "output", 3),
            node("sub", "subagent", 4, {"role": "sql"}),
            node("db", "database", 5, {"connection_id": cid}),
        ],
        "edges": [
            {"source": "input", "target": "agent"},
            {"source": "agent", "target": "output"},
            {"source": "db", "target": "sub"},  # through the subagent, as drawn
            {"source": "sub", "target": "agent"},
        ],
    }
    r = await client.put(f"/api/v1/assistants/{aid}/draft-graph", json=graph, headers=org_headers)
    assert r.status_code == 200 and r.json()["validation"]["errors"] == [], r.text

    # What the Panels tab does: edit a setting and PUT the whole config.
    cfg = (await client.get(f"/api/v1/assistants/{aid}", headers=org_headers)).json()[
        "draft_config"
    ]
    cfg["system_prompt"] = "Be brief."
    r = await client.put(f"/api/v1/assistants/{aid}/draft-config", json=cfg, headers=org_headers)
    assert r.status_code == 200, r.text

    after = (await client.get(f"/api/v1/assistants/{aid}", headers=org_headers)).json()
    nodes = {n["id"]: n for n in after["draft_graph"]["nodes"]}
    assert "db" in nodes, f"the database node was renamed: {sorted(nodes)}"
    assert nodes["db"]["position"] == {"x": 5, "y": 5}, "the database node moved"
    assert nodes["sub"]["position"] == {"x": 4, "y": 4}
    edges = {(e["source"], e["target"]) for e in after["draft_graph"]["edges"]}
    assert ("db", "sub") in edges, "the user's wiring was replaced"
    assert after["draft_config"]["system_prompt"] == "Be brief."
