"""`POST /assistants/{id}/prompt:generate` (task 5.5).

A suggestion, never a save: the draft config is untouched. Editors only.
The model path runs on the official SDK over a mock transport
(`tests/claude_stub.py`); nothing reaches the real API.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app.agent import claude_api
from app.db.session import get_sessionmaker
from app.models.assistant import Assistant
from app.models.integration import DbConnection, DbEngine
from app.models.usage import UsageEvent
from httpx import AsyncClient
from sqlalchemy import select
from tests import claude_stub

from test_orgs import _register  # type: ignore[import-not-found]

DESCRIPTION = "Answers customers' questions about orders and returns."


async def _assistant(client: AsyncClient, headers: dict[str, str], **config: Any) -> str:
    a = (
        await client.post("/api/v1/assistants", json={"name": "Shop Helper"}, headers=headers)
    ).json()
    if config:
        draft = {**a["draft_config"], **config}
        r = await client.put(
            f"/api/v1/assistants/{a['id']}/draft-config", json=draft, headers=headers
        )
        assert r.status_code == 200, r.text
    return str(a["id"])


def _url(aid: str) -> str:
    return f"/api/v1/assistants/{aid}/prompt:generate"


async def _draft_config(client: AsyncClient, headers: dict[str, str], aid: str) -> Any:
    return (await client.get(f"/api/v1/assistants/{aid}", headers=headers)).json()["draft_config"]


async def _usage(aid: str) -> list[UsageEvent]:
    async with get_sessionmaker()() as s:
        rows = await s.scalars(select(UsageEvent).where(UsageEvent.assistant_id == uuid.UUID(aid)))
        return list(rows.all())


@pytest.fixture
def paid(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real-model path; `.answer` is what the stand-in API sends back."""
    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: True)
    state: Any = type("State", (), {})()
    state.answer = claude_stub.message(
        json.dumps({"system_prompt": "You are Shop Helper.", "rules": ["Be kind."]}),
        tokens_in=900,
        tokens_out=400,
        model="claude-sonnet-5",
    )
    state.stub = claude_stub.install(monkeypatch, lambda r: state.answer)
    return state


async def test_the_free_path_writes_from_a_template_and_saves_nothing(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers, rag={"enabled": True})
    before = await _draft_config(client, org_headers, aid)

    r = await client.post(_url(aid), json={"description": DESCRIPTION}, headers=org_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["source"], body["model"], body["cost_usd"]) == ("template", None, 0)
    assert body["system_prompt"].startswith("You are Shop Helper.")
    assert DESCRIPTION in body["system_prompt"]
    assert "search the knowledge base first" in body["system_prompt"], "read the draft config"
    assert body["rules"]

    assert await _draft_config(client, org_headers, aid) == before, "a suggestion, not a save"
    assert await _usage(aid) == [], "nothing spent"


async def test_a_model_draft_names_the_wiring_and_goes_on_the_ledger(
    client: AsyncClient, org_headers: dict[str, str], paid: Any
) -> None:
    aid = await _assistant(client, org_headers)
    async with get_sessionmaker()() as s:
        org_id = (await s.get_one(Assistant, uuid.UUID(aid))).org_id
        used, unused = (
            DbConnection(
                assistant_id=uuid.UUID(aid),
                org_id=org_id,
                name=name,
                engine=DbEngine.postgres,
                database="shop",
            )
            for name in ("Shop PG", "Old warehouse")
        )
        s.add_all([used, unused])
        await s.commit()
        used_id = str(used.id)
    draft = await _draft_config(client, org_headers, aid)
    draft["databases"] = [{"connection_id": used_id}]
    r = await client.put(f"/api/v1/assistants/{aid}/draft-config", json=draft, headers=org_headers)
    assert r.status_code == 200, r.text

    r = await client.post(
        _url(aid),
        json={"description": DESCRIPTION, "current_prompt": "You are a bot."},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "system_prompt": "You are Shop Helper.",
        "rules": ["Be kind."],
        "source": "model",
        "model": "claude-sonnet-5-5",
        "cost_usd": pytest.approx(900 * 2 / 1e6 + 400 * 10 / 1e6),
    }
    prompt = claude_stub.prompt(paid.stub.requests[0])
    assert "Shop PG (postgres)" in prompt, "the databases the draft uses, by name"
    assert "Old warehouse" not in prompt, "not every connection on the assistant"
    assert "You are a bot." in prompt

    (row,) = await _usage(aid)
    assert (row.model, row.tokens_in, row.tokens_out) == ("claude-sonnet-5-5", 900, 400)


async def test_a_refusal_is_a_clear_422_and_still_on_the_ledger(
    client: AsyncClient, org_headers: dict[str, str], paid: Any
) -> None:
    paid.answer = claude_stub.message(
        "I can't help with that.", tokens_in=700, stop_reason="refusal", model="claude-sonnet-5"
    )
    aid = await _assistant(client, org_headers)
    r = await client.post(_url(aid), json={"description": DESCRIPTION}, headers=org_headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "prompt_refused"
    assert [row.tokens_in for row in await _usage(aid)] == [700], "billed all the same"


@pytest.mark.parametrize(
    ("answer", "status", "code"),
    [
        (claude_stub.error(401, "authentication_error"), 502, "model_auth_failed"),
        (claude_stub.error(429, "rate_limit_error"), 503, "model_unavailable"),
        (claude_stub.error(529, "overloaded_error"), 503, "model_unavailable"),
        (claude_stub.error(500, "api_error"), 503, "model_unavailable"),
        (claude_stub.error(400, "invalid_request_error"), 502, "model_error"),
        (
            claude_stub.message('{"system_prompt": "You', stop_reason="max_tokens"),
            502,
            "prompt_unreadable",
        ),
    ],
)
async def test_model_failures_are_typed_and_never_leak_the_raw_error(
    client: AsyncClient,
    org_headers: dict[str, str],
    paid: Any,
    answer: Any,
    status: int,
    code: str,
) -> None:
    paid.answer = answer
    aid = await _assistant(client, org_headers)
    r = await client.post(_url(aid), json={"description": DESCRIPTION}, headers=org_headers)
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    assert "stub" not in r.text and "You" not in r.json()["error"]["message"]


async def test_only_editors_may_ask_and_the_description_is_checked(client: AsyncClient) -> None:
    owner = await _register(client, "assist-owner@example.com")
    member = await _register(client, "assist-member@example.com")
    outsider = await _register(client, "assist-outsider@example.com")
    org = (
        await client.post("/api/v1/orgs", json={"name": "Assist Team"}, headers=owner.headers)
    ).json()
    inv = await client.post(
        f"/api/v1/orgs/{org['id']}/invites",
        json={"email": "assist-member@example.com", "role": "member"},
        headers=owner.headers,
    )
    token = inv.json()["accept_url"].rsplit("/", 1)[-1]
    assert (
        await client.post(f"/api/v1/invites/{token}/accept", headers=member.headers)
    ).status_code == 200
    o = {**owner.headers, "X-Org-Id": org["id"]}
    m = {**member.headers, "X-Org-Id": org["id"]}
    aid = await _assistant(client, o)

    denied = await client.post(_url(aid), json={"description": DESCRIPTION}, headers=m)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "not_assistant_editor"
    hidden = await client.post(
        _url(aid), json={"description": DESCRIPTION}, headers=outsider.headers
    )
    assert hidden.status_code == 404
    short = await client.post(_url(aid), json={"description": "Helps."}, headers=o)
    assert short.status_code == 422
