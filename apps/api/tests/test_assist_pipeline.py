"""Recommending a starter pipeline from a description (task 5.6).

The model path runs on the official SDK over a mock transport
(`tests/claude_stub.py`); nothing reaches the real API.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app.agent import claude_api
from app.assist.pipeline import Inventory, Named, build, changes, merge_rules, recommend
from app.assist.structured import AssistFailed
from app.db.session import get_sessionmaker
from app.models.assistant import Assistant
from app.models.integration import DbConnection, DbEngine
from app.models.usage import UsageEvent
from app.schemas.assistant_config import AssistantConfig
from httpx import AsyncClient
from sqlalchemy import select
from tests import claude_stub

from test_orgs import _register  # type: ignore[import-not-found]

DB = Named(id="11111111-1111-1111-1111-111111111111", name="Shop PG", detail="postgres")
OLD_DB = Named(id="22222222-2222-2222-2222-222222222222", name="Warehouse", detail="mysql")
TICKETS = Named(id="33333333-3333-3333-3333-333333333333", name="tickets", detail="create_ticket")


def _plan(**over: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "knowledge_base": False,
        "web_search": False,
        "calculator": False,
        "datetime": False,
        "http_domains": [],
        "databases": [],
        "mcp_servers": [],
        "subagents": [],
        "memory_tool": False,
        "system_prompt": "You are Shop Helper.",
        "rules": ["Be kind."],
        "reasons": [],
    }
    return {**plan, **over}


@pytest.fixture
def paid(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real-model path; `.answer` is what the stand-in API sends back."""
    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: True)
    state: Any = type("State", (), {})()
    state.answer = claude_stub.message(
        json.dumps(_plan()), tokens_in=1500, tokens_out=600, model="claude-sonnet-5"
    )
    state.stub = claude_stub.install(monkeypatch, lambda r: state.answer)
    return state


@pytest.fixture
def free(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: False)
    monkeypatch.setattr(claude_api, "client_factory", lambda _t: pytest.fail("no API calls"))


# ── the free path ────────────────────────────────────────────


@pytest.mark.usefixtures("free")
async def test_the_template_picks_from_the_words_used() -> None:
    rec = await recommend(
        AssistantConfig(),
        name="HR Helper",
        description="Answers employees' questions from our HR handbook, and works out leave dates.",
    )
    assert rec.source == "template" and rec.spend is None
    c = rec.config
    assert c.rag.enabled and c.tools.datetime.enabled
    assert not (c.tools.web_search.enabled or c.tools.calculator.enabled or c.memory.memory_tool)
    assert c.system_prompt.startswith("You are HR Helper.")
    assert "search the knowledge base first" in c.system_prompt
    assert c.guardrails.rules
    assert [(x.key, bool(x.why)) for x in rec.capabilities] == [
        ("knowledge_base", True),
        ("datetime", True),
    ]


@pytest.mark.usefixtures("free")
async def test_the_template_uses_what_the_assistant_already_has() -> None:
    inv = Inventory(
        data_sources=["faq.pdf"], databases=[DB, OLD_DB], mcp_servers=[TICKETS], offline=True
    )
    rec = await recommend(
        AssistantConfig(),
        name="A",
        description="Looks things up in Shop PG, files tickets, and checks the latest news online.",
        inventory=inv,
    )
    c = rec.config
    assert c.rag.enabled, "it has data sources to search"
    assert [d.connection_id for d in c.databases] == [DB.id], "only the one it names"
    assert [m.id for m in c.mcp_servers] == [TICKETS.id]
    assert c.mcp_servers[0].tools == [], "nothing allowed until someone chooses"
    assert not c.tools.web_search.enabled, "the instance is offline"
    mcp = next(x for x in rec.capabilities if x.key.startswith("mcp:"))
    assert mcp.label == "MCP server: tickets" and "Choose which of its tools" in (mcp.why or "")


# ── the model path ───────────────────────────────────────────


async def test_the_model_chooses_and_the_platform_builds(paid: Any) -> None:
    paid.answer = claude_stub.message(
        json.dumps(
            _plan(
                knowledge_base=True,
                databases=["shop pg", "Invented DB"],
                http_domains=["https://API.GitHub.com/repos", "not a domain", "localhost"],
                subagents=["sql", "research", "retrieval"],
                memory_tool=True,
                system_prompt="  You are Shop Helper.  ",
                rules=["Be kind.", "Be   kind."],
                reasons=[
                    {"capability": "knowledge_base", "why": "Returns policy lives in docs."},
                    {"capability": "databases", "why": "Orders are in the database."},
                ],
            )
        ),
        tokens_in=1500,
        tokens_out=600,
    )
    inv = Inventory(data_sources=[], databases=[DB], mcp_servers=[TICKETS])
    rec = await recommend(
        AssistantConfig(), name="Shop Helper", description="Orders and returns.", inventory=inv
    )
    prompt = claude_stub.prompt(paid.stub.requests[0])
    assert "- Shop PG (postgres)" in prompt and "- tickets (create_ticket)" in prompt
    assert "<description>\nOrders and returns.\n</description>" in prompt
    schema = claude_stub.body(paid.stub.requests[0])["output_config"]["format"]["schema"]
    assert "subagents" in schema["properties"]

    c = rec.config
    assert [d.connection_id for d in c.databases] == [DB.id], "unknown names are dropped"
    assert c.tools.http_request.enabled
    assert c.tools.http_request.allowed_domains == ["api.github.com"]
    assert (c.subagents.sql, c.subagents.retrieval, c.subagents.research) == (True, True, False)
    assert c.memory.memory_tool
    assert c.system_prompt == "You are Shop Helper." and c.guardrails.rules == ["Be kind."]
    why = {x.key: x.why for x in rec.capabilities}
    assert why["knowledge_base"] == "Returns policy lives in docs."
    assert why[f"database:{DB.id}"] == "Orders are in the database."
    assert rec.spend is not None and rec.spend.tokens_in == 1500


async def test_a_refusal_is_reported_with_its_cost(paid: Any) -> None:
    paid.answer = claude_stub.message("I can't.", stop_reason="refusal", tokens_in=300)
    with pytest.raises(AssistFailed) as failed:
        await recommend(AssistantConfig(), name="A", description="Helps people.")
    assert failed.value.reason == "refused" and failed.value.spend.tokens_in == 300


# ── building and describing ──────────────────────────────────


def test_building_keeps_what_the_plan_does_not_choose() -> None:
    from app.assist.pipeline import _Plan

    base = AssistantConfig.model_validate(
        {
            "models": {"main": {"model": "claude-opus-5"}},
            "guardrails": {"rules": ["Never share prices."], "pii_redaction": False},
            "databases": [{"connection_id": DB.id, "expose_write": True}],
            "rag": {"retrieval": {"rerank_top_n": 5}},
        }
    )
    plan = _Plan.model_validate(_plan(databases=["Shop PG"], rules=["never share prices", "Hi."]))
    built = build(base, plan, Inventory(databases=[DB]))
    assert built.models.main.model == "claude-opus-5"
    assert built.guardrails.pii_redaction is False
    assert built.guardrails.rules == ["Never share prices.", "Hi."]
    assert built.databases[0].expose_write is True, "its settings are kept"
    assert built.rag.retrieval.rerank_top_n == 5


def test_the_built_config_is_in_its_saved_form() -> None:
    """Chosen in name order, stored in id order: what the preview shows is
    exactly what saving it will hold."""
    from app.assist.pipeline import _Plan

    accounts = Named(id="44444444-4444-4444-4444-444444444444", name="Accounts")
    plan = _Plan.model_validate(_plan(databases=["Accounts", "Shop PG"]))
    built = build(AssistantConfig(), plan, Inventory(databases=[accounts, DB]))
    assert [d.connection_id for d in built.databases] == [DB.id, accounts.id]
    assert built == AssistantConfig.model_validate(built.model_dump())


def test_changes_say_what_applying_would_do() -> None:
    before = AssistantConfig.model_validate(
        {
            "tools": {"web_search": {"enabled": True}},
            "databases": [{"connection_id": OLD_DB.id}],
        }
    )
    after = AssistantConfig.model_validate(
        {
            "system_prompt": "You are new.",
            "guardrails": {"rules": ["A.", "B."]},
            "rag": {"enabled": True},
            "databases": [{"connection_id": DB.id}],
            "subagents": {"sql": True},
        }
    )
    assert changes(before, after, Inventory(databases=[DB, OLD_DB])) == [
        "Replaces the system prompt.",
        "Adds 2 rules.",
        "Adds the knowledge base.",
        "Removes web search.",
        "Adds the database Shop PG.",
        "Removes the database Warehouse.",
        "Adds the sql subagent.",
    ]
    assert changes(before, before, Inventory()) == []


def test_merge_rules_keeps_every_existing_rule() -> None:
    assert merge_rules(["Be brief."], ["be  brief", "Ask first.", ""]) == [
        "Be brief.",
        "Ask first.",
    ]


# ── the routes ───────────────────────────────────────────────

DESCRIPTION = "Answers customers' questions about orders from our docs and the Shop PG database."


async def _assistant(client: AsyncClient, headers: dict[str, str]) -> str:
    a = await client.post("/api/v1/assistants", json={"name": "Shop Helper"}, headers=headers)
    return str(a.json()["id"])


async def _usage(org_id: str) -> list[UsageEvent]:
    async with get_sessionmaker()() as s:
        rows = await s.scalars(select(UsageEvent).where(UsageEvent.org_id == uuid.UUID(org_id)))
        return list(rows.all())


@pytest.mark.usefixtures("free")
async def test_recommending_for_an_assistant_previews_and_saves_nothing(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    async with get_sessionmaker()() as s:
        a = await s.get_one(Assistant, uuid.UUID(aid))
        db = DbConnection(
            assistant_id=a.id,
            org_id=a.org_id,
            name="Shop PG",
            engine=DbEngine.postgres,
            database="x",
        )
        s.add(db)
        await s.commit()
        db_id = str(db.id)
    # The builder moved the agent node; a recommendation must not undo that.
    detail = (await client.get(f"/api/v1/assistants/{aid}", headers=org_headers)).json()
    graph = detail["draft_graph"]
    for n in graph["nodes"]:
        if n["type"] == "agent":
            n["position"] = {"x": 999.0, "y": 555.0}
    await client.put(f"/api/v1/assistants/{aid}/draft-graph", json=graph, headers=org_headers)
    before = (await client.get(f"/api/v1/assistants/{aid}", headers=org_headers)).json()

    r = await client.post(
        f"/api/v1/assistants/{aid}/pipeline:recommend",
        json={"description": DESCRIPTION},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "template" and body["cost_usd"] == 0
    assert body["config"]["rag"]["enabled"] is True
    assert body["config"]["databases"] == [
        {
            "connection_id": db_id,
            "nl2sql": True,
            "expose_write": False,
            "agent": True,
            "subagents": [],
        }
    ]
    assert "Adds the database Shop PG." in body["changes"]
    assert "Adds the knowledge base." in body["changes"]
    types = sorted(n["type"] for n in body["graph"]["nodes"])
    assert types.count("database") == 1 and "knowledge_base" in types
    agent = next(n for n in body["graph"]["nodes"] if n["type"] == "agent")
    assert agent["position"] == {"x": 999.0, "y": 555.0}
    assert body["validation"]["errors"] == []

    after = (await client.get(f"/api/v1/assistants/{aid}", headers=org_headers)).json()
    assert after["draft_config"] == before["draft_config"], "a suggestion, not a save"
    assert await _usage(org_headers["X-Org-Id"]) == []

    # Applying it is the normal config save.
    saved = await client.put(
        f"/api/v1/assistants/{aid}/draft-config", json=body["config"], headers=org_headers
    )
    assert saved.status_code == 200, saved.text
    assert sorted(n["type"] for n in saved.json()["graph"]["nodes"]) == types


async def test_the_guided_setup_can_ask_before_the_assistant_exists(
    client: AsyncClient, org_headers: dict[str, str], paid: Any
) -> None:
    paid.answer = claude_stub.message(
        json.dumps(_plan(calculator=True, databases=["Shop PG"])), tokens_in=800
    )
    r = await client.post(
        "/api/v1/pipeline:recommend",
        json={"description": "Quotes delivery prices for customers.", "name": "Quoter"},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config"]["tools"]["calculator"]["enabled"] is True
    assert body["config"]["databases"] == [], "nothing registered to wire in yet"
    assert body["changes"] == [] and body["validation"]["errors"] == []
    assert "Assistant name: Quoter" in claude_stub.prompt(paid.stub.requests[0])
    (row,) = await _usage(org_headers["X-Org-Id"])
    assert (row.assistant_id, row.tokens_in) == (None, 800)

    no_org = {"Authorization": org_headers["Authorization"]}
    missing = await client.post(
        "/api/v1/pipeline:recommend", json={"description": "Quotes prices."}, headers=no_org
    )
    assert missing.status_code == 400


async def test_a_refused_recommendation_is_a_clear_422_and_billed(
    client: AsyncClient, org_headers: dict[str, str], paid: Any
) -> None:
    paid.answer = claude_stub.message("No.", stop_reason="refusal", tokens_in=250)
    aid = await _assistant(client, org_headers)
    r = await client.post(
        f"/api/v1/assistants/{aid}/pipeline:recommend",
        json={"description": DESCRIPTION},
        headers=org_headers,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "pipeline_refused"
    assert "recommend a pipeline" in r.json()["error"]["message"]
    assert [u.tokens_in for u in await _usage(org_headers["X-Org-Id"])] == [250]


@pytest.mark.usefixtures("free")
async def test_only_editors_may_ask_for_an_assistant(client: AsyncClient) -> None:
    owner = await _register(client, "pipe-owner@example.com")
    member = await _register(client, "pipe-member@example.com")
    org = (
        await client.post("/api/v1/orgs", json={"name": "Pipe Team"}, headers=owner.headers)
    ).json()
    inv = await client.post(
        f"/api/v1/orgs/{org['id']}/invites",
        json={"email": "pipe-member@example.com", "role": "member"},
        headers=owner.headers,
    )
    token = inv.json()["accept_url"].rsplit("/", 1)[-1]
    await client.post(f"/api/v1/invites/{token}/accept", headers=member.headers)
    o = {**owner.headers, "X-Org-Id": org["id"]}
    m = {**member.headers, "X-Org-Id": org["id"]}
    aid = await _assistant(client, o)
    url = f"/api/v1/assistants/{aid}/pipeline:recommend"
    denied = await client.post(url, json={"description": DESCRIPTION}, headers=m)
    assert denied.status_code == 403
    # A member may still use the guided setup for an assistant of their own.
    own = await client.post(
        "/api/v1/pipeline:recommend", json={"description": DESCRIPTION}, headers=m
    )
    assert own.status_code == 200
    short = await client.post(url, json={"description": "Short."}, headers=o)
    assert short.status_code == 422
