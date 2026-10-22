"""How Chat uses your data (Phase 8.7, plans D5 and D18).

Before their first message in Chat, everyone reads and accepts the notice:
the server refuses the message until they have, for the version in force.
The owner edits the text: a typo fixed keeps the version, a real change
asks everyone again. Where messages go is read from the server's settings.
Each person sees when an admin opened one of their conversations, and
why. An assistant's card says what its approved version uses and sends
out.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.audit_log import AuditLog
from app.preflight import Report, review_privacy
from app.services import conversation_reads, privacy
from httpx import AsyncClient
from sqlalchemy import select
from tests.versions import go_live

from test_audiences import Org, _org  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture(autouse=True)
def _notice_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "privacy_notice_required", True)


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


async def _live(client: AsyncClient, org: Org) -> str:
    h = org.h("builder")
    aid = (await client.post(f"{API}/assistants", json={"name": "Helpdesk"}, headers=h)).json()[
        "id"
    ]
    await go_live(client, aid, h, approver=org.h("owner"))
    return str(aid)


async def _conversation(client: AsyncClient, org: Org, who: str, aid: str, **body: Any) -> str:
    r = await client.post(f"{API}/assistants/{aid}/conversations", json=body, headers=org.h(who))
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _say(client: AsyncClient, org: Org, who: str, cid: str) -> Any:
    return await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hello"}, headers=org.h(who)
    )


async def _accept(client: AsyncClient, org: Org, who: str, version: int) -> Any:
    return await client.post(
        f"{API}/chat/notice:accept", json={"version": version}, headers=org.h(who)
    )


# ── before the first message ─────────────────────────────────


async def test_the_built_in_notice_is_version_one_and_names_the_owner(
    client: AsyncClient, org: Org
) -> None:
    r = await client.get(f"{API}/chat/notice", headers=org.h("emp1"))
    assert r.status_code == 200, r.text
    notice = r.json()
    assert (notice["version"], notice["accepted_at"], notice["required"]) == (1, None, True)
    assert notice["body"] == privacy.DEFAULT_BODY
    assert "Admins and the owner can open any conversation" in notice["body"]
    assert "password" in notice["body"]
    assert notice["contact"] == "owner@example.com"
    assert notice["processors"][0]["name"].startswith("Anthropic")


async def test_the_first_message_waits_for_the_notice(client: AsyncClient, org: Org) -> None:
    aid = await _live(client, org)
    cid = await _conversation(client, org, "emp1", aid)
    refused = await _say(client, org, "emp1", cid)
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "notice_required")

    accepted = await _accept(client, org, "emp1", 1)
    assert accepted.status_code == 200 and accepted.json()["accepted_at"]
    assert (await _say(client, org, "emp1", cid)).status_code == 200
    # Once is enough, and accepting again changes nothing.
    assert (await _accept(client, org, "emp1", 1)).json()["accepted_at"] == accepted.json()[
        "accepted_at"
    ]
    async with get_sessionmaker()() as s:
        rows = list(
            await s.scalars(select(AuditLog).where(AuditLog.action == "privacy_notice.accept"))
        )
    assert len(rows) == 1 and rows[0].meta == {"version": 1}


async def test_everyone_in_chat_accepts_it_but_previews_and_others_dont_wait(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    # The owner's own live conversation is kept and readable too.
    mine = await _conversation(client, org, "owner", aid)
    assert (await _say(client, org, "owner", mine)).json()["error"]["code"] == "notice_required"
    # A preview in the Studio doesn't ask.
    preview = await _conversation(client, org, "builder", aid, mode="preview")
    assert (await _say(client, org, "builder", preview)).status_code == 200
    # One person's acceptance is theirs.
    await _accept(client, org, "emp1", 1)
    other = await _conversation(client, org, "emp2", aid)
    assert (await _say(client, org, "emp2", other)).status_code == 403


# ── the owner's text ─────────────────────────────────────────


async def _save(client: AsyncClient, org: Org, body: str, *, ask_again: bool, who: str = "owner"):
    return await client.put(
        f"{API}/orgs/{org.id}/notice",
        json={"body": body, "ask_again": ask_again},
        headers=org.h(who),
    )


async def test_a_typo_keeps_the_version_and_a_real_change_asks_again(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid = await _conversation(client, org, "emp1", aid)
    await _accept(client, org, "emp1", 1)

    fixed = await _save(client, org, privacy.DEFAULT_BODY + " Thanks.", ask_again=False)
    assert fixed.status_code == 200, fixed.text
    assert (fixed.json()["version"], fixed.json()["revision"], fixed.json()["accepted"]) == (
        1,
        1,
        1,
    )
    assert (await _say(client, org, "emp1", cid)).status_code == 200

    changed = await _save(client, org, "  New rules: ask IT first.  ", ask_again=True)
    assert (changed.json()["version"], changed.json()["revision"]) == (2, 2)
    assert changed.json()["body"] == "New rules: ask IT first."
    assert changed.json()["accepted"] == 0
    assert (await _say(client, org, "emp1", cid)).json()["error"]["code"] == "notice_required"
    # A page still showing the old text can't accept it.
    stale = await _accept(client, org, "emp1", 1)
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "notice_changed")
    assert (await _accept(client, org, "emp1", 2)).status_code == 200

    revisions = (
        await client.get(f"{API}/orgs/{org.id}/notice/revisions", headers=org.h("owner"))
    ).json()
    assert [(r["revision"], r["version"]) for r in revisions] == [(2, 2), (1, 1)]
    assert revisions[1]["body"].endswith("Thanks.") and revisions[0]["created_by"]
    async with get_sessionmaker()() as s:
        metas = [
            a.meta
            for a in await s.scalars(
                select(AuditLog)
                .where(AuditLog.action == "privacy_notice.update")
                .order_by(AuditLog.created_at)
            )
        ]
    assert [m["ask_again"] for m in metas] == [False, True]


async def test_only_the_owner_edits_it_and_admins_read_it(client: AsyncClient, org: Org) -> None:
    promoted = await client.patch(
        f"{API}/orgs/{org.id}/members/{org.people['builder2'].user_id}",
        json={"role": "admin"},
        headers=org.h("owner"),
    )
    assert promoted.status_code == 200, promoted.text
    for who in ("builder", "builder2"):
        assert (await _save(client, org, "x", ask_again=True, who=who)).status_code == 403, who
    admin_view = (await client.get(f"{API}/orgs/{org.id}/notice", headers=org.h("builder2"))).json()
    assert admin_view["can_edit"] is False and admin_view["version"] == 1
    assert (await client.get(f"{API}/orgs/{org.id}/notice", headers=org.h("emp1"))).status_code in (
        403,
        404,
    )
    got = (await client.get(f"{API}/orgs/{org.id}/notice", headers=org.h("owner"))).json()
    assert got["custom"] is False and got["can_edit"] is True
    assert got["default_body"] == privacy.DEFAULT_BODY
    too_long = await _save(client, org, "x" * (privacy.BODY_MAX + 1), ask_again=False)
    assert too_long.status_code == 422


async def test_the_privacy_contact_is_a_setting_that_falls_back_to_the_owner(
    client: AsyncClient, org: Org
) -> None:
    url = f"{API}/orgs/{org.id}/settings"
    r = await client.patch(
        url, json={"privacy_contact": "Privacy@Acme.example"}, headers=org.h("owner")
    )
    assert r.status_code == 200, r.text
    notice = (await client.get(f"{API}/chat/notice", headers=org.h("emp1"))).json()
    assert notice["contact"] == "privacy@acme.example"
    bad = await client.patch(url, json={"privacy_contact": "not an email"}, headers=org.h("owner"))
    assert bad.status_code == 422
    await client.patch(url, json={"privacy_contact": ""}, headers=org.h("owner"))
    notice = (await client.get(f"{API}/chat/notice", headers=org.h("emp1"))).json()
    assert notice["contact"] == "owner@example.com"


# ── where messages go ────────────────────────────────────────


def test_where_messages_go_follows_the_servers_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "voyage_api_key": "voyage-secret-value",
        "rag_offline": False,
        "langfuse_host": "https://trace.acme.example",
        "google_client_id": "client-id-value",
        "smtp_host": "smtp.gmail.com",
    }.items():
        monkeypatch.setattr(settings, key, value)
    names = [p.name for p in privacy.processors()]
    assert names[0].startswith("Anthropic")
    assert "Voyage AI" in names and "Langfuse (trace.acme.example)" in names
    assert "Google" in names and "Email (smtp.gmail.com)" in names
    words = " ".join(f"{p.name} {p.what}" for p in privacy.processors())
    assert "voyage-secret-value" not in words and "client-id-value" not in words

    monkeypatch.setattr(settings, "rag_offline", True)
    monkeypatch.setattr(settings, "langfuse_host", "")
    names = [p.name for p in privacy.processors()]
    assert "Voyage AI" not in names and not any(n.startswith("Langfuse") for n in names)


def test_preflight_warns_when_production_doesnt_ask() -> None:
    report = Report()
    review_privacy(SimpleNamespace(is_production=True, privacy_notice_required=False), report)
    assert any(f.name == "PRIVACY_NOTICE_REQUIRED" for f in report.findings)
    quiet = Report()
    review_privacy(SimpleNamespace(is_production=True, privacy_notice_required=True), quiet)
    assert not quiet.findings


# ── admins' reads, shown to the person read ──────────────────


async def test_each_person_sees_when_an_admin_opened_their_conversation(
    client: AsyncClient, org: Org
) -> None:
    aid = await _live(client, org)
    cid = await _conversation(client, org, "emp1", aid)
    read = await client.get(
        f"{API}/conversations/{cid}",
        headers={**org.h("owner"), conversation_reads.HEADER: quote("Ticket 4411: wrong answer")},
    )
    assert read.status_code == 200, read.text

    mine = (await client.get(f"{API}/chat/access", headers=org.h("emp1"))).json()["items"]
    assert len(mine) == 1
    entry = mine[0]
    assert entry["conversation_id"] == cid and entry["reason"] == "Ticket 4411: wrong answer"
    assert entry["reader"] == "owner@example.com" and entry["reader_role"] == "owner"
    others = (await client.get(f"{API}/chat/access", headers=org.h("emp2"))).json()["items"]
    assert others == []


# ── an assistant's card ──────────────────────────────────────


async def test_the_card_says_what_the_approved_version_uses_and_sends_out(
    client: AsyncClient, org: Org
) -> None:
    h = org.h("builder")
    a = (await client.post(f"{API}/assistants", json={"name": "Helpdesk"}, headers=h)).json()
    db = await client.post(
        f"{API}/assistants/{a['id']}/db-connections",
        json={
            "name": "Tickets",
            "engine": "postgres",
            "host": "db",
            "database": "x",
            "permissions": {"read": True, "write": True, "ddl": False},
        },
        headers=h,
    )
    assert db.status_code == 201, db.text
    cfg = a["draft_config"]
    cfg["databases"] = [{"connection_id": db.json()["id"], "nl2sql": True, "expose_write": True}]
    cfg["tools"]["web_search"]["enabled"] = True
    cfg["tools"]["http_request"].update(enabled=True, allowed_domains=["docs.acme.example"])
    cfg["memory"]["memory_tool"] = True
    r = await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=h)
    assert r.status_code == 200, r.text
    await go_live(client, a["id"], h, approver=org.h("owner"))

    card = (
        await client.get(f"{API}/chat/assistants/{a['id']}/about", headers=org.h("emp1"))
    ).json()
    assert card["version_number"] == 1 and card["approved_at"]
    assert card["approved_by"] == "owner" and card["owner"] == "builder"
    lookups = {u["label"]: u["approval"] for u in card["lookups"]}
    assert lookups["Looks up data in Tickets"] is False
    assert "Searches the web" in lookups
    assert lookups["Reads pages on docs.acme.example"] is True
    assert {u["label"]: u["approval"] for u in card["actions"]} == {"Changes data in Tickets": True}
    assert card["memory_tool"] is True
    leaves = [p["name"] for p in card["leaves"]]
    assert leaves[0].startswith("Anthropic")
    assert "docs.acme.example" in leaves and "Web search, through Anthropic" in leaves
    # Nothing of how it is built: no prompt, no connection details.
    assert "system_prompt" not in card and "db" not in str(card["lookups"])

    # The draft changing reaches nobody until it is approved.
    cfg["tools"]["web_search"]["enabled"] = False
    await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=h)
    again = (
        await client.get(f"{API}/chat/assistants/{a['id']}/about", headers=org.h("emp1"))
    ).json()
    assert any(u["label"] == "Searches the web" for u in again["lookups"])
