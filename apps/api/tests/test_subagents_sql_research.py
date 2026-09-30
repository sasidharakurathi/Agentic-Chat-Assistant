"""The sql and research subagents, and per-role models (task 5.1).

Retrieval arrived in Phase 2 (`test_agent_subagents.py`). What is new here:

- **sql** gets the database tools this turn really has, only the family
  that is wired (a Postgres-only assistant is not handed mongo_find);
- **research** gets web search, plus kb_search when there is a knowledge
  base, and does not exist at all without web search (switched off, or
  offline mode);
- each role can have its own model, set on its canvas node or in Panels,
  instead of one node's settings silently winning for all of them;
- a write run by the sql subagent still asks a person: its calls go through
  the same permission callback as the main agent's.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from app.agent.options import build_runtime_spec, compose_system_prompt
from app.agent.subagents import (
    RESEARCH_MAX_TURNS,
    SQL_MAX_TURNS,
    SQL_TOOLS,
    build_agent_definitions,
    build_subagent_specs,
)
from app.config import settings
from app.graph.compile import compile_graph
from app.graph.project import project_config
from app.schemas.assistant_config import AssistantConfig
from httpx import AsyncClient

import test_chat_approvals as approval_tests  # type: ignore[import-not-found]
from test_chat_approvals import (  # type: ignore[import-not-found]
    _assistant_with_db,
    _await_prompt,
    _conversation,
    _resolve,
    _run_turn,
)

# The approval tests' stubbed SQL tool, as a fixture of this module too.
fake_sql = approval_tests.fake_sql

AID = uuid.uuid4()
DB = "11111111-1111-1111-1111-111111111111"
MONGO = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _online(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests read the repo's .env, which may say RAG_OFFLINE=1; these need a
    known mode. The offline tests switch it on themselves."""
    monkeypatch.setattr(settings, "rag_offline", False)


def _config(**over: Any) -> AssistantConfig:
    return AssistantConfig.model_validate(over)


def _names(spec: Any) -> list[str]:
    return [s.name for s in spec.subagents]


# ── sql ──────────────────────────────────────────────────────


def test_sql_needs_its_toggle_and_a_database() -> None:
    assert build_subagent_specs(_config(subagents={"sql": True})) == []
    assert build_subagent_specs(_config(databases=[{"connection_id": DB}])) == []
    (spec,) = build_subagent_specs(
        _config(subagents={"sql": True}, databases=[{"connection_id": DB}])
    )
    assert (spec.name, spec.tools, spec.max_turns) == ("sql", SQL_TOOLS, SQL_MAX_TURNS)


def test_sql_gets_only_the_database_family_that_is_wired() -> None:
    config = _config(subagents={"sql": True}, databases=[{"connection_id": DB}])
    spec = build_runtime_spec(config, assistant_id=AID, db_engines={DB: "postgres"})
    (sub,) = spec.subagents
    assert sub.tools == [
        "mcp__caps__sql_list_schemas",
        "mcp__caps__sql_introspect",
        "mcp__caps__sql_query",
    ]
    mongo = _config(subagents={"sql": True}, databases=[{"connection_id": MONGO}])
    spec = build_runtime_spec(mongo, assistant_id=AID, db_engines={MONGO: "mongodb"})
    assert spec.subagents[0].tools == ["mcp__caps__mongo_find", "mcp__caps__mongo_aggregate"]


def test_a_subagent_without_its_tools_this_turn_is_left_out() -> None:
    """No assistant id (a compile dry run) means no database tools, so no sql
    subagent, and no line telling the main agent to delegate to it."""
    config = _config(subagents={"sql": True}, databases=[{"connection_id": DB}])
    spec = build_runtime_spec(config)
    assert spec.subagents == []
    assert "`sql` subagent" not in spec.system_prompt
    assert "`sql` subagent" in build_runtime_spec(config, assistant_id=AID).system_prompt


# ── research ─────────────────────────────────────────────────


def test_research_gets_web_search_and_the_knowledge_base() -> None:
    config = _config(
        subagents={"research": True},
        tools={"web_search": {"enabled": True}},
        rag={"enabled": True},
    )
    spec = build_runtime_spec(config, assistant_id=AID)
    (sub,) = spec.subagents
    assert (sub.name, sub.max_turns) == ("research", RESEARCH_MAX_TURNS)
    assert sub.tools == ["WebSearch", "mcp__caps__kb_search"]
    assert "`research` subagent" in spec.system_prompt


def test_research_without_a_knowledge_base_only_searches_the_web() -> None:
    config = _config(subagents={"research": True}, tools={"web_search": {"enabled": True}})
    (sub,) = build_runtime_spec(config, assistant_id=AID).subagents
    assert sub.tools == ["WebSearch"]


def test_research_does_not_exist_without_web_search() -> None:
    """With only the knowledge base it would be retrieval with a longer
    prompt; the main agent would delegate to a subagent that can't do what
    its description promises."""
    config = _config(subagents={"research": True}, rag={"enabled": True})
    assert build_runtime_spec(config, assistant_id=AID).subagents == []


def test_offline_mode_removes_research(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline switches web search off (EXPLAINER §10.13); research must not
    bring it back through the side door."""
    monkeypatch.setattr(settings, "rag_offline", True)
    config = _config(
        subagents={"research": True},
        tools={"web_search": {"enabled": True}},
        rag={"enabled": True},
    )
    spec = build_runtime_spec(config, assistant_id=AID)
    assert spec.subagents == []
    assert "WebSearch" not in spec.enabled_tools
    assert "`research` subagent" not in spec.system_prompt


# ── per-role models ──────────────────────────────────────────


def test_each_role_has_its_own_model_or_the_shared_one() -> None:
    config = _config(
        subagents={
            "retrieval": True,
            "sql": True,
            "models": {"sql": {"model": "claude-sonnet-5", "effort": "medium", "max_turns": 12}},
        },
        rag={"enabled": True},
        databases=[{"connection_id": DB}],
        models={"subagent": {"model": "claude-haiku-4-5", "effort": "low"}},
    )
    specs = {s.name: s for s in build_subagent_specs(config)}
    assert (specs["sql"].model, specs["sql"].effort, specs["sql"].max_turns) == (
        "claude-sonnet-5",
        "medium",
        12,
    )
    assert (specs["retrieval"].model, specs["retrieval"].effort) == ("claude-haiku-4-5", "low")
    definitions = build_agent_definitions(list(specs.values()))
    assert definitions["sql"].model == "claude-sonnet-5"
    assert definitions["sql"].maxTurns == 12


def test_role_models_round_trip_through_the_canvas() -> None:
    """Two roles with different settings: before 5.1 compile applied one
    node's to all of them, and projection dropped them."""
    config = _config(
        subagents={
            "retrieval": True,
            "sql": True,
            "research": True,
            "models": {
                "retrieval": {"model": "claude-haiku-4-5", "max_turns": 3},
                "sql": {"model": "claude-sonnet-5", "effort": "medium"},
            },
        },
        rag={"enabled": True},
        databases=[{"connection_id": DB}],
        tools={"web_search": {"enabled": True}},
    )
    graph = project_config(config)
    nodes = {n.data.role: n.data for n in graph.nodes if n.type == "subagent"}
    assert nodes["retrieval"].max_turns == 3
    assert nodes["sql"].model is not None and nodes["sql"].model.model == "claude-sonnet-5"
    assert nodes["research"].model is None and nodes["research"].max_turns is None
    again = compile_graph(graph)
    assert again.subagents == config.subagents


def test_the_prompt_names_only_the_subagents_the_turn_has() -> None:
    config = _config(
        subagents={"retrieval": True, "sql": True, "research": True},
        rag={"enabled": True},
    )
    prompt = compose_system_prompt(
        config, AID, build_runtime_spec(config, assistant_id=AID).subagents
    )
    assert "`retrieval` subagent" in prompt
    assert "`sql` subagent" not in prompt
    assert "`research` subagent" not in prompt


# ── in a conversation (fake driver) ──────────────────────────


async def _with_sql_subagent(client: AsyncClient, headers: dict[str, str]) -> str:
    aid = await _assistant_with_db(client, headers)
    a = (await client.get(f"/api/v1/assistants/{aid}", headers=headers)).json()
    cfg = a["draft_config"]
    cfg["subagents"] = {**cfg["subagents"], "sql": True}
    r = await client.put(f"/api/v1/assistants/{aid}/draft-config", json=cfg, headers=headers)
    assert r.status_code == 200, r.text
    return aid


@pytest.mark.anyio
async def test_the_sql_subagent_runs_a_query_nested_under_its_delegation(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _with_sql_subagent(client, org_headers)
    cid = await _conversation(client, org_headers, aid)
    events: list[Any] = []
    await _run_turn(cid, "sql: SELECT count(*) FROM users", events)

    calls = [e for e in events if e.type == "tool_call"]
    assert [(c.name, c.parent_id) for c in calls] == [
        ("Agent", None),
        ("mcp__caps__sql_query", calls[0].id),
    ]
    assert calls[0].input["subagent_type"] == "sql"
    assert fake_sql["ran"] == ["SELECT count(*) FROM users"]
    # The subagent's findings are the delegation's result, not the answer.
    delegation = next(e for e in events if e.type == "tool_result" and e.id == calls[0].id)
    assert delegation.status == "success" and "1 row(s) affected" in delegation.output


@pytest.mark.anyio
async def test_a_write_from_inside_the_subagent_still_asks_a_person(
    client: AsyncClient, org_headers: dict[str, str], fake_sql: dict[str, Any]
) -> None:
    aid = await _with_sql_subagent(client, org_headers)
    cid = await _conversation(client, org_headers, aid)
    events: list[Any] = []
    turn = asyncio.create_task(_run_turn(cid, "sql: DELETE FROM sessions", events))
    prompt = await _await_prompt(events)
    assert prompt.rationale == "DELETE FROM sessions"
    assert fake_sql["ran"] == [], "nothing runs before a person decides"

    await _resolve(client, org_headers, prompt.approval_id, "denied")
    await asyncio.wait_for(turn, timeout=15)
    assert fake_sql["ran"] == []
    nested = next(e for e in events if e.type == "tool_result" and e.parent_id is not None)
    assert nested.status == "denied"
