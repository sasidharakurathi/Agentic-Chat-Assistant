"""The `/meta` endpoints the frontend builds its editors from.

`graph-schema` (task 2.13) matters more than it looks: the canvas decides
which connections a user is allowed to draw from it. If it drifted from the
validator, the failure would be silent in both directions — the canvas
offering an edge the API then rejects, or refusing one that is actually
legal. These tests assert the endpoint IS the validator's own constants,
not a copy that happens to agree today.
"""

from __future__ import annotations

from typing import get_args

import pytest
from app.agent import claude_api
from app.config import settings
from app.graph.nodes import NodeType
from app.graph.validate import ALLOWED_EDGES, SINGLETON_TYPES
from httpx import AsyncClient


async def test_config_schema_exposes_a_schema_and_a_usable_default(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/meta/config-schema")).json()
    assert body["schema"]["type"] == "object"
    assert body["default"]["rag"]["enabled"] is False
    assert "claude-sonnet-5" in body["allowed_models"]


@pytest.mark.parametrize("offline", [True, False])
async def test_config_schema_says_whether_the_instance_is_offline(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, offline: bool
) -> None:
    """The web-search settings warn when offline mode has switched it off."""
    monkeypatch.setattr(settings, "rag_offline", offline)
    body = (await client.get("/api/v1/meta/config-schema")).json()
    assert body["offline"] is offline


@pytest.mark.parametrize("real", [True, False])
async def test_config_schema_says_whether_ai_helpers_bill(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, real: bool
) -> None:
    """The prompt writer says up front whether generating costs money."""
    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: real)
    body = (await client.get("/api/v1/meta/config-schema")).json()
    assert body["real_model"] is real


async def test_graph_schema_matches_the_validators_own_rules(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/meta/graph-schema")
    assert resp.status_code == 200
    body = resp.json()

    assert sorted(body["node_types"]) == sorted(get_args(NodeType))
    assert {tuple(e) for e in body["allowed_edges"]} == ALLOWED_EDGES
    assert sorted(body["singleton_types"]) == sorted(SINGLETON_TYPES)


async def test_graph_schema_carries_the_rag_wiring_the_canvas_needs(
    client: AsyncClient,
) -> None:
    """data_source -> knowledge_base -> agent is the chain task 2.13 draws."""
    body = (await client.get("/api/v1/meta/graph-schema")).json()
    edges = {tuple(e) for e in body["allowed_edges"]}
    assert ("data_source", "knowledge_base") in edges
    assert ("knowledge_base", "agent") in edges
    # ...and the shortcut that would bypass the knowledge base is not legal.
    assert ("data_source", "agent") not in edges


async def test_graph_schema_is_json_serialisable_tuples_become_lists(
    client: AsyncClient,
) -> None:
    """ALLOWED_EDGES is a set of tuples; JSON has neither. The endpoint has to
    hand the frontend a stable, sorted list of pairs."""
    body = (await client.get("/api/v1/meta/graph-schema")).json()
    assert all(isinstance(e, list) and len(e) == 2 for e in body["allowed_edges"])
    assert body["allowed_edges"] == sorted(body["allowed_edges"])
