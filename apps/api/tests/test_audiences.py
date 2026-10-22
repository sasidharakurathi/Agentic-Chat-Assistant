"""Audiences, groups and the catalog (Phase 8.5, plans D3 and D16).

An assistant's listing (category, starters, audience) is the builder's
draft until a version is submitted; then it is frozen with the version and
reviewed with it, and the live version's listing decides who sees the
assistant in Chat and how. The owner, admins and the assistant's builders
always see it. Group membership changes take effect at once. Someone
dropped from the audience keeps their history and can't post any more.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from tests.test_migrations import _alembic
from tests.test_version_review_migration import _insert
from tests.versions import go_live, submit

from test_orgs import Actor, _register  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


class Org:
    def __init__(self, org_id: str, people: dict[str, Actor], group_id: str) -> None:
        self.id = org_id
        self.people = people
        self.group_id = group_id

    def h(self, who: str) -> dict[str, str]:
        return {**self.people[who].headers, "X-Org-Id": self.id}


async def _org(client: AsyncClient) -> Org:
    """An owner, two builders, two employees, and a group "IT" holding the
    first employee."""
    owner = await _register(client, "owner@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Acme"}, headers=owner.headers)).json()
    people = {"owner": owner}
    for name, role in (
        ("builder", "member"),
        ("builder2", "member"),
        ("emp1", "employee"),
        ("emp2", "employee"),
    ):
        actor = await _register(client, f"{name}@example.com")
        inv = await client.post(
            f"{API}/orgs/{org['id']}/invites",
            json={"email": f"{name}@example.com", "role": role},
            headers=owner.headers,
        )
        token = inv.json()["accept_url"].rsplit("/", 1)[-1]
        assert (
            await client.post(f"{API}/invites/{token}/accept", headers=actor.headers)
        ).is_success
        people[name] = actor
    o = {**owner.headers, "X-Org-Id": org["id"]}
    group = (
        await client.post(f"{API}/orgs/{org['id']}/groups", json={"name": "IT"}, headers=o)
    ).json()
    added = await client.post(
        f"{API}/orgs/{org['id']}/groups/{group['id']}/members",
        json={"user_id": people["emp1"].user_id},
        headers=o,
    )
    assert added.status_code == 201, added.text
    return Org(org["id"], people, group["id"])


@pytest.fixture
async def org(client: AsyncClient) -> Org:
    return await _org(client)


async def _assistant(client: AsyncClient, org: Org, name: str = "Helpdesk", **listing: Any) -> str:
    h = org.h("builder")
    aid = (await client.post(f"{API}/assistants", json={"name": name}, headers=h)).json()["id"]
    if listing:
        r = await client.put(f"{API}/assistants/{aid}/listing", json=listing, headers=h)
        assert r.status_code == 200, r.text
    await go_live(client, aid, h, approver=org.h("owner"))
    return str(aid)


async def _seen(client: AsyncClient, org: Org, who: str, **params: str) -> list[str]:
    r = await client.get(f"{API}/chat/assistants", params=params, headers=org.h(who))
    assert r.status_code == 200, r.text
    return [a["name"] for a in r.json()]


# ── who sees it ──────────────────────────────────────────────


async def test_by_default_everyone_sees_it(client: AsyncClient, org: Org) -> None:
    await _assistant(client, org)
    for who in ("emp1", "emp2", "builder2"):
        assert await _seen(client, org, who) == ["Helpdesk"], who


async def test_a_group_audience_is_seen_by_its_group_and_those_who_run_it(
    client: AsyncClient, org: Org
) -> None:
    aid = await _assistant(client, org, audience={"kind": "groups", "group_ids": [org.group_id]})
    assert await _seen(client, org, "emp1") == ["Helpdesk"]
    assert await _seen(client, org, "emp2") == []
    assert await _seen(client, org, "builder2") == [], "builders are bound by it too"
    for who in ("owner", "builder"):
        assert await _seen(client, org, who) == ["Helpdesk"], "admins and its builders always"
    # Not there for the others at all: the card and a new conversation.
    assert (
        await client.get(f"{API}/chat/assistants/{aid}", headers=org.h("emp2"))
    ).status_code == 404
    started = await client.post(
        f"{API}/assistants/{aid}/conversations", json={}, headers=org.h("emp2")
    )
    assert started.status_code == 404


async def test_joining_the_group_takes_effect_at_once(client: AsyncClient, org: Org) -> None:
    await _assistant(client, org, audience={"kind": "groups", "group_ids": [org.group_id]})
    await client.post(
        f"{API}/orgs/{org.id}/groups/{org.group_id}/members",
        json={"user_id": org.people["emp2"].user_id},
        headers=org.h("owner"),
    )
    assert await _seen(client, org, "emp2") == ["Helpdesk"]


async def test_named_people(client: AsyncClient, org: Org) -> None:
    await _assistant(
        client, org, audience={"kind": "people", "user_ids": [org.people["emp2"].user_id]}
    )
    assert await _seen(client, org, "emp2") == ["Helpdesk"]
    assert await _seen(client, org, "emp1") == []


async def test_dropped_from_the_audience_keeps_history_not_the_assistant(
    client: AsyncClient, org: Org
) -> None:
    aid = await _assistant(client, org, audience={"kind": "groups", "group_ids": [org.group_id]})
    h = org.h("emp1")
    cid = (await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=h)).json()[
        "id"
    ]
    await client.delete(
        f"{API}/orgs/{org.id}/groups/{org.group_id}/members/{org.people['emp1'].user_id}",
        headers=org.h("owner"),
    )
    assert (await client.get(f"{API}/conversations/{cid}", headers=h)).status_code == 200
    r = await client.post(f"{API}/conversations/{cid}/messages", json={"text": "hi"}, headers=h)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "not_in_audience")


# ── what was reviewed is what shows ──────────────────────────


async def test_the_listing_is_frozen_with_the_version(client: AsyncClient, org: Org) -> None:
    aid = await _assistant(client, org, category="IT", starters=["My VPN drops", "  "])
    h = org.h("builder")
    # Draft changes, and a rename, reach nobody until the next approval.
    await client.put(
        f"{API}/assistants/{aid}/listing",
        json={"audience": {"kind": "people", "user_ids": [org.people["emp2"].user_id]}},
        headers=h,
    )
    await client.patch(f"{API}/assistants/{aid}", json={"name": "Renamed"}, headers=h)
    card = (await client.get(f"{API}/chat/assistants/{aid}", headers=org.h("emp1"))).json()
    assert (card["name"], card["category"], card["starters"]) == (
        "Helpdesk",
        "IT",
        ["My VPN drops"],
    )

    n = await submit(client, aid, h)
    version = (
        await client.get(f"{API}/assistants/{aid}/versions/{n}", headers=org.h("owner"))
    ).json()
    assert version["listing"]["audience"]["kind"] == "people", "the reviewer sees who it reaches"
    assert version["listing"]["name"] == "Renamed"


async def test_the_listing_is_checked(client: AsyncClient, org: Org) -> None:
    h = org.h("builder")
    aid = (await client.post(f"{API}/assistants", json={"name": "X"}, headers=h)).json()["id"]
    url = f"{API}/assistants/{aid}/listing"
    empty = await client.put(url, json={"audience": {"kind": "groups"}}, headers=h)
    assert (empty.status_code, empty.json()["error"]["code"]) == (400, "audience_empty")
    stranger = {"kind": "groups", "group_ids": [str(uuid.uuid4())]}
    r = await client.put(url, json={"audience": stranger}, headers=h)
    assert (r.status_code, r.json()["error"]["code"]) == (400, "audience_unknown")
    too_many = await client.put(url, json={"starters": ["a", "b", "c", "d", "e"]}, headers=h)
    assert too_many.status_code == 422
    other = await client.put(url, json={"category": "x"}, headers=org.h("builder2"))
    assert other.status_code == 403, "its builders' draft"


# ── the catalog ──────────────────────────────────────────────


async def test_official_first_then_search_and_category(client: AsyncClient, org: Org) -> None:
    await _assistant(client, org, name="Alpha", category="HR")
    beta = await _assistant(client, org, name="Beta", category="IT")
    r = await client.post(
        f"{API}/assistants/{beta}:official", json={"official": True}, headers=org.h("builder")
    )
    assert r.status_code == 403, "an admin's to give"
    r = await client.post(
        f"{API}/assistants/{beta}:official", json={"official": True}, headers=org.h("owner")
    )
    assert r.status_code == 200 and r.json()["official_at"]
    assert await _seen(client, org, "emp1") == ["Beta", "Alpha"]
    assert await _seen(client, org, "emp1", q="alp") == ["Alpha"]
    assert await _seen(client, org, "emp1", category="it") == ["Beta"]


# ── groups ───────────────────────────────────────────────────


async def test_groups_are_admins_and_a_used_one_stays(client: AsyncClient, org: Org) -> None:
    base = f"{API}/orgs/{org.id}/groups"
    listed = await client.get(base, headers=org.h("builder"))
    assert [g["name"] for g in listed.json()] == ["IT"], "builders see names, to choose"
    assert (
        await client.post(base, json={"name": "HR"}, headers=org.h("builder"))
    ).status_code == 403
    dup = await client.post(base, json={"name": "IT"}, headers=org.h("owner"))
    assert (dup.status_code, dup.json()["error"]["code"]) == (409, "group_exists")
    await _assistant(client, org, audience={"kind": "groups", "group_ids": [org.group_id]})
    gone = await client.delete(f"{base}/{org.group_id}", headers=org.h("owner"))
    assert (gone.status_code, gone.json()["error"]["code"]) == (409, "group_in_use")
    stranger = await client.post(
        f"{base}/{org.group_id}/members",
        json={"user_id": str(uuid.uuid4())},
        headers=org.h("owner"),
    )
    assert (stranger.status_code, stranger.json()["error"]["code"]) == (400, "not_a_member")


# ── the upgrade ──────────────────────────────────────────────


def test_versions_from_before_are_for_everyone(tmp_path: Path) -> None:
    db_path = tmp_path / "aud.db"
    assert (r := _alembic(db_path, "upgrade", "c8a4c1d2e3f4")).returncode == 0, r.stderr
    org, aid, vid = (uuid.uuid4().hex for _ in range(3))
    db = sqlite3.connect(db_path)
    _insert(db, "organizations", id=org)
    _insert(db, "assistants", id=aid, org_id=org, name="Desk", description="Help")
    _insert(db, "assistant_versions", id=vid, assistant_id=aid, org_id=org, version_number=1)
    db.commit()
    db.close()
    assert (r := _alembic(db_path, "upgrade", "c8a5d1e2f3a4")).returncode == 0, r.stderr
    db = sqlite3.connect(db_path)
    (listing,) = db.execute("SELECT listing FROM assistant_versions").fetchone()
    db.close()
    assert '"kind": "everyone"' in listing and '"name": "Desk"' in listing
