"""Subagents with their own capabilities, and the router (task 5.10).

On the canvas an edge from a capability to a subagent means the capability
is that subagent's: the main agent can't use it. The compiler records who
may use what, the projection draws it back, the tool gate enforces it per
call, and each subagent is offered what is wired to it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.agent import claude_api, router, scope
from app.agent.hooks import build_tool_gate
from app.agent.options import build_runtime_spec, compose_system_prompt
from app.agent.subagents import build_subagent_specs
from app.graph.compile import compile_graph
from app.graph.nodes import (
    AgentNode,
    DatabaseNode,
    DatabaseNodeData,
    Edge,
    Graph,
    InputNode,
    KnowledgeBaseNode,
    OutputNode,
    RouterNode,
    RouterNodeData,
    SubagentNode,
    SubagentNodeData,
    ToolNode,
    ToolNodeData,
)
from app.graph.project import project_config
from app.graph.validate import validate_graph
from app.schemas.assistant_config import AssistantConfig, ModelSpec
from httpx import AsyncClient
from tests import claude_stub

DB = "11111111-1111-1111-1111-111111111111"
OTHER_DB = "22222222-2222-2222-2222-222222222222"
MCP = "33333333-3333-3333-3333-333333333333"

# ── the canvas: edges become scopes, and back ────────────────


def _graph(*extra: Any, edges: list[tuple[str, str]]) -> Graph:
    return Graph(
        nodes=[InputNode(id="in"), AgentNode(id="a"), OutputNode(id="out"), *extra],
        edges=[
            Edge(source="in", target="a"),
            Edge(source="a", target="out"),
            *(Edge(source=s, target=t) for s, t in edges),
        ],
    )


def _sub(role: str) -> SubagentNode:
    return SubagentNode(id=f"sub:{role}", data=SubagentNodeData(role=role))  # type: ignore[arg-type]


def _db(cid: str = DB) -> DatabaseNode:
    return DatabaseNode(id=f"db:{cid}", data=DatabaseNodeData(connection_id=cid))


def test_a_database_wired_to_the_sql_subagent_is_its_alone() -> None:
    cfg = compile_graph(
        _graph(_db(), _sub("sql"), edges=[("db:" + DB, "sub:sql"), ("sub:sql", "a")])
    )
    (db,) = cfg.databases
    assert (db.agent, db.subagents) == (False, ["sql"])
    assert cfg.subagents.sql


def test_wired_to_both_it_is_both_of_theirs() -> None:
    cfg = compile_graph(
        _graph(
            _db(),
            _sub("sql"),
            edges=[("db:" + DB, "a"), ("db:" + DB, "sub:sql"), ("sub:sql", "a")],
        )
    )
    assert (cfg.databases[0].agent, cfg.databases[0].subagents) == (True, ["sql"])


def test_a_subagent_left_unwired_takes_its_capabilities_with_it() -> None:
    cfg = compile_graph(_graph(_db(), _sub("sql"), edges=[("db:" + DB, "sub:sql")]))
    assert cfg.databases == [] and not cfg.subagents.sql


def test_the_knowledge_base_can_be_the_retrieval_subagents() -> None:
    kb = KnowledgeBaseNode(id="kb")
    cfg = compile_graph(
        _graph(kb, _sub("retrieval"), edges=[("kb", "sub:retrieval"), ("sub:retrieval", "a")])
    )
    assert cfg.rag.enabled and (cfg.rag.agent, cfg.rag.subagents) == (False, ["retrieval"])


def test_the_projection_draws_scopes_back_as_edges() -> None:
    cfg = AssistantConfig.model_validate(
        {
            "databases": [{"connection_id": DB, "agent": False, "subagents": ["sql"]}],
            "tools": {"calculator": {"enabled": True, "subagents": ["sql"]}},
            "subagents": {"sql": True},
        }
    )
    graph = project_config(cfg)
    pairs = {(e.source, e.target) for e in graph.edges}
    assert (f"db:{DB}", "sub:sql") in pairs and (f"db:{DB}", "agent") not in pairs
    assert ("tool:calculator", "agent") in pairs and ("tool:calculator", "sub:sql") in pairs
    assert compile_graph(graph) == cfg


def test_turning_a_subagent_off_hands_its_capabilities_back() -> None:
    cfg = AssistantConfig.model_validate(
        {
            "databases": [{"connection_id": DB, "agent": False, "subagents": ["sql"]}],
            "subagents": {"sql": False},
        }
    )
    assert (cfg.databases[0].agent, cfg.databases[0].subagents) == (True, [])


def test_the_sql_subagent_is_warned_about_a_database_it_cant_use() -> None:
    only_research = _graph(
        _db(),
        _sub("sql"),
        _sub("research"),
        ToolNode(id="tool:web_search", data=ToolNodeData(key="web_search")),
        edges=[
            ("db:" + DB, "sub:research"),
            ("tool:web_search", "a"),
            ("sub:sql", "a"),
            ("sub:research", "a"),
        ],
    )
    codes = [w.code for w in validate_graph(only_research).warnings]
    assert "subagent_without_database" in codes
    own = _graph(_db(), _sub("sql"), edges=[("db:" + DB, "sub:sql"), ("sub:sql", "a")])
    assert "subagent_without_database" not in [w.code for w in validate_graph(own).warnings]


# ── who may make a call ──────────────────────────────────────


def _scoped_config() -> AssistantConfig:
    return AssistantConfig.model_validate(
        {
            "rag": {"enabled": True, "agent": False, "subagents": ["research"]},
            "databases": [
                {"connection_id": DB, "agent": False, "subagents": ["sql"]},
                {"connection_id": OTHER_DB},
            ],
            "tools": {
                "web_search": {"enabled": True},
                "calculator": {"enabled": True, "agent": False, "subagents": ["sql"]},
            },
            "mcp_servers": [{"id": MCP, "tools": ["create"], "agent": False, "subagents": ["sql"]}],
            "subagents": {"retrieval": True, "sql": True, "research": True},
        }
    )


@pytest.mark.parametrize(
    ("caller", "tool", "tool_input", "allowed"),
    [
        (None, "mcp__caps__sql_query", {"connection_id": DB}, False),
        ("sql", "mcp__caps__sql_query", {"connection_id": DB}, True),
        ("research", "mcp__caps__sql_query", {"connection_id": DB}, False),
        (None, "mcp__caps__sql_query", {"connection_id": OTHER_DB}, True),
        ("sql", "mcp__caps__sql_query", {"connection_id": OTHER_DB}, True),  # the agent's
        (None, "mcp__caps__kb_search", {}, False),
        ("research", "mcp__caps__kb_search", {}, True),
        ("retrieval", "mcp__caps__kb_search", {}, False),
        (None, "mcp__caps__calculator", {}, False),
        ("sql", "mcp__caps__calculator", {}, True),
        (None, "mcp__tickets__create", {}, False),
        ("sql", "mcp__tickets__create", {}, True),
        (None, "WebSearch", {}, True),
        (None, "mcp__caps__memory", {}, True),  # not a scoped capability
    ],
)
def test_each_call_is_checked_against_who_is_calling(
    caller: str | None, tool: str, tool_input: dict[str, Any], allowed: bool
) -> None:
    reason = scope.refusal(
        _scoped_config(), caller, tool, tool_input, {"mcp__tickets__create": MCP}
    )
    assert (reason is None) is allowed, reason


def test_the_refusal_tells_the_main_agent_whom_to_ask() -> None:
    reason = scope.refusal(
        _scoped_config(), None, "mcp__caps__sql_query", {"connection_id": DB}, {}
    )
    assert reason == (
        "This database is only available to the sql subagent: delegate to it rather than "
        "calling mcp__caps__sql_query yourself."
    )


def test_the_main_agent_is_only_offered_what_it_may_use() -> None:
    cfg = _scoped_config()
    tools = [
        "mcp__caps__sql_query",
        "mcp__caps__kb_search",
        "mcp__caps__calculator",
        "WebSearch",
        "mcp__tickets__create",
    ]
    mine = scope.main_agent_tools(cfg, tools, {"mcp__tickets__create": MCP})
    # sql_query stays: one of the databases (OTHER_DB) is the agent's.
    assert mine == ["mcp__caps__sql_query", "WebSearch"]
    only_sub = cfg.model_copy(deep=True)
    only_sub.databases = [only_sub.databases[0]]
    assert "mcp__caps__sql_query" not in scope.main_agent_tools(only_sub, tools, {})


def test_each_subagent_is_offered_what_is_wired_to_it() -> None:
    cfg = _scoped_config()
    available = {
        "mcp__caps__kb_search",
        "mcp__caps__kb_list_sources",
        "mcp__caps__sql_list_schemas",
        "mcp__caps__sql_introspect",
        "mcp__caps__sql_query",
        "mcp__caps__calculator",
        "mcp__tickets__create",
        "WebSearch",
    }
    ids = {"mcp__tickets__create": MCP}
    specs = {
        s.name: s
        for s in build_subagent_specs(
            cfg,
            available=available,
            usable=lambda role, tool: scope.can_use(cfg, role, tool, ids),
            extras=lambda role: scope.extra_tools(cfg, role, available, ids),  # type: ignore[arg-type]
        )
    }
    assert "retrieval" not in specs, "the knowledge base is research's: nothing to search"
    assert {"mcp__caps__calculator", "mcp__tickets__create"} <= set(specs["sql"].tools)
    assert "mcp__caps__sql_query" in specs["sql"].tools
    assert set(specs["research"].tools) == {
        "WebSearch",
        "mcp__caps__kb_search",
        "mcp__caps__kb_list_sources",  # the whole knowledge base is wired to it
    }


def test_the_main_prompt_only_mentions_what_the_main_agent_has() -> None:
    cfg = AssistantConfig.model_validate(
        {
            "rag": {"enabled": True, "agent": False, "subagents": ["retrieval"]},
            "subagents": {"retrieval": True},
        }
    )
    import uuid

    from app.agent.options import _KB_PROMPT_CITED, _RETRIEVAL_SUBAGENT_PROMPT

    aid = uuid.uuid4()
    spec = build_runtime_spec(cfg, assistant_id=aid)
    assert "mcp__caps__kb_search" in spec.enabled_tools, "the tool exists (for the subagent)"
    assert "mcp__caps__kb_search" not in (spec.main_tools or [])
    prompt = compose_system_prompt(cfg, aid, main_tools=spec.main_tools)
    assert _KB_PROMPT_CITED not in prompt, "not told to search it itself"
    assert _RETRIEVAL_SUBAGENT_PROMPT in prompt, "told to delegate instead"


async def test_the_gate_reads_the_caller_from_the_hook_input() -> None:
    cfg = _scoped_config()
    gate = build_tool_gate(
        frozenset({"mcp__caps__sql_query"}),
        scope=lambda tool, args, caller: scope.refusal(cfg, caller, tool, args, {}),
    )
    call = {"tool_name": "mcp__caps__sql_query", "tool_input": {"connection_id": DB}}
    main = await gate(call, None, None)
    assert main["hookSpecificOutput"]["permissionDecision"] == "deny"
    sub = await gate({**call, "agent_id": "a-1", "agent_type": "sql"}, None, None)
    assert sub == {}, "no decision: the normal flow (and approvals) go on"
    # agent_type alone is the main thread of an --agent session, not a subagent.
    assert (await gate({**call, "agent_type": "sql"}, None, None))["hookSpecificOutput"][
        "permissionDecision"
    ] == "deny"


# ── the router ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("level", "agent", "effort"),
    [
        ("simple", "high", "low"),
        ("normal", "medium", "medium"),
        ("hard", "medium", "high"),
        ("hard", "max", "max"),  # never lowered
    ],
)
def test_effort_by_level(level: Any, agent: Any, effort: str) -> None:
    assert router.effort_for(level, agent) == effort


@pytest.mark.parametrize(
    ("message", "level"),
    [
        ("thanks!", "simple"),
        ("Hello there, how are you?", "simple"),
        ("ok", "simple"),
        ("What are your opening hours on Sunday?", "normal"),
        ("Compare the two plans and explain the trade-offs step by step.", "hard"),
        ("word " * 90, "hard"),
    ],
)
def test_the_free_rules(message: str, level: str) -> None:
    assert router.by_rules(message) == level


def _router_config(**over: Any) -> AssistantConfig:
    return AssistantConfig.model_validate(
        {
            "router": {"enabled": True},
            "models": {
                "main": {"model": "claude-sonnet-5", "effort": "medium"},
                "router": {"model": "claude-haiku-4-5", "effort": "low"},
            },
            **over,
        }
    )


async def test_the_router_model_sorts_on_the_real_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: True)
    stub = claude_stub.install(
        monkeypatch,
        lambda r: claude_stub.message(json.dumps({"level": "hard"}), tokens_in=120, tokens_out=5),
    )
    routing = await router.route(_router_config(), "What's the weather like?")
    assert (routing.level, routing.effort, routing.source) == ("hard", "high", "model")
    sent = claude_stub.body(stub.requests[0])
    assert (sent["model"], sent["max_tokens"]) == ("claude-haiku-4-5", 200)
    assert "<message>\nWhat's the weather like?\n</message>" in claude_stub.prompt(stub.requests[0])
    assert routing.spend is not None and routing.spend.model == "claude-haiku-4-5"


async def test_a_failing_router_never_fails_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: True)
    claude_stub.install(monkeypatch, lambda r: claude_stub.error(529, "overloaded_error"))
    routing = await router.route(_router_config(), "thanks")
    assert (routing.level, routing.effort, routing.source) == ("normal", "medium", "fallback")


async def _turn(
    client: AsyncClient, headers: dict[str, str], cfg: dict[str, Any], text: str
) -> Any:
    a = (await client.post("/api/v1/assistants", json={"name": "Routed"}, headers=headers)).json()
    r = await client.put(
        f"/api/v1/assistants/{a['id']}/draft-config",
        json={**a["draft_config"], **cfg},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    c = (
        await client.post(f"/api/v1/assistants/{a['id']}/conversations", json={}, headers=headers)
    ).json()
    async with client.stream(
        "POST", f"/api/v1/conversations/{c['id']}/messages", json={"text": text}, headers=headers
    ) as resp:
        events = [
            json.loads(line[6:]) async for line in resp.aiter_lines() if line.startswith("data: ")
        ]
    runs = (await client.get(f"/api/v1/conversations/{c['id']}/runs", headers=headers)).json()
    return events, runs["items"][0]


async def test_a_turn_runs_at_the_routed_effort(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    cfg = {
        "router": {"enabled": True},
        "models": {
            **(await _default_models(client, org_headers)),
            "main": {"model": "claude-sonnet-5", "effort": "medium"},
        },
    }
    _, run = await _turn(client, org_headers, cfg, "thanks!")
    assert (run["route"], run["effort"]) == ("simple", "low")
    _, run = await _turn(
        client, org_headers, cfg, "Compare these two options step by step, please."
    )
    assert (run["route"], run["effort"]) == ("hard", "high")
    _, run = await _turn(client, org_headers, {"router": {"enabled": False}}, "thanks!")
    assert run["route"] is None, "no router, no routing"


async def _default_models(client: AsyncClient, headers: dict[str, str]) -> Any:
    schema = (await client.get("/api/v1/meta/config-schema")).json()
    return schema["default"]["models"]


async def test_the_main_agent_cant_use_a_tool_wired_only_to_a_subagent(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """The calculator is the sql subagent's: asked to calculate, the main
    agent doesn't reach for it."""
    cfg = {
        "tools": {"calculator": {"enabled": True, "agent": False, "subagents": ["sql"]}},
        "databases": [],
        "subagents": {"sql": True},
    }
    events, _ = await _turn(client, org_headers, cfg, "what is 12 * 7?")
    assert "tool_call" not in [e["type"] for e in events]
    agent_calc = {"tools": {"calculator": {"enabled": True}}}
    events, _ = await _turn(client, org_headers, agent_calc, "what is 12 * 7?")
    assert [e["name"] for e in events if e["type"] == "tool_call"] == ["mcp__caps__calculator"]


def test_a_wired_router_turns_routing_on_and_carries_its_model() -> None:
    r = RouterNode(id="r", data=RouterNodeData(model=ModelSpec(model="claude-opus-5")))
    g = Graph(
        nodes=[InputNode(id="in"), r, AgentNode(id="a"), OutputNode(id="out")],
        edges=[
            Edge(source="in", target="r"),
            Edge(source="r", target="a"),
            Edge(source="a", target="out"),
        ],
    )
    cfg = compile_graph(g)
    assert cfg.router.enabled and cfg.models.router.model == "claude-opus-5"
    projected = project_config(cfg)
    types = [n.type for n in projected.nodes]
    assert types.count("router") == 1
    assert compile_graph(projected) == cfg
