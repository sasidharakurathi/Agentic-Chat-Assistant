"""Privilege escalation between org members (F-8).

The role-change route was gated by "admin or better" and nothing else, so an
admin could PATCH their *own* membership to owner and then demote the
founding owner — both calls returned 200. `RequireOwner` was declared and
applied to no route at all. And any member could rename, archive, or post
into any other member's conversation, with no audit entry.

The rule now lives in the service, where both the actor and the target are
visible: you cannot grant a role above your own, you cannot change someone
ranked at or above you (owners excepted), and anyone may lower their own
role. Conversation mutations belong to their creator alone: admins read
other people's conversations but cannot post into or change them (7a.4).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from httpx import AsyncClient
from tests.test_orgs import Actor, _register

pytestmark = pytest.mark.anyio


@dataclass
class Team:
    org_id: str
    owner: Actor
    admin: Actor
    admin2: Actor
    member: Actor
    member2: Actor

    def h(self, actor: Actor) -> dict[str, str]:
        return {**actor.headers, "X-Org-Id": self.org_id}


async def _team(client: AsyncClient) -> Team:
    owner = await _register(client, "owner@example.com")
    org = (await client.post("/api/v1/orgs", json={"name": "Team"}, headers=owner.headers)).json()
    people: dict[str, Actor] = {}
    for name, role in (
        ("admin", "admin"),
        ("admin2", "admin"),
        ("member", "member"),
        ("member2", "member"),
    ):
        actor = await _register(client, f"{name}@example.com")
        inv = await client.post(
            f"/api/v1/orgs/{org['id']}/invites",
            json={"email": f"{name}@example.com", "role": role},
            headers=owner.headers,
        )
        assert inv.status_code == 201, inv.text
        token = inv.json()["accept_url"].rsplit("/", 1)[-1]
        assert (
            await client.post(f"/api/v1/invites/{token}/accept", headers=actor.headers)
        ).is_success
        people[name] = actor
    return Team(org_id=org["id"], owner=owner, **people)


async def _set_role(client: AsyncClient, team: Team, actor: Actor, target: Actor, role: str):
    return await client.patch(
        f"/api/v1/orgs/{team.org_id}/members/{target.user_id}",
        json={"role": role},
        headers=actor.headers,
    )


async def _roles(client: AsyncClient, team: Team) -> dict[str, str]:
    rows = (
        await client.get(f"/api/v1/orgs/{team.org_id}/members", headers=team.owner.headers)
    ).json()["items"]
    return {r["user_id"]: r["role"] for r in rows}


# ── role changes ─────────────────────────────────────────────


async def test_an_admin_cannot_make_themselves_owner(client: AsyncClient) -> None:
    """The reproduced escalation: this used to return 200."""
    t = await _team(client)
    r = await _set_role(client, t, t.admin, t.admin, "owner")
    assert r.status_code == 403
    assert (await _roles(client, t))[t.admin.user_id] == "admin"


async def test_an_admin_cannot_demote_the_owner(client: AsyncClient) -> None:
    """The second half of the takeover: this also returned 200."""
    t = await _team(client)
    r = await _set_role(client, t, t.admin, t.owner, "member")
    assert r.status_code == 403
    assert (await _roles(client, t))[t.owner.user_id] == "owner"


async def test_an_admin_cannot_mint_an_owner(client: AsyncClient) -> None:
    t = await _team(client)
    assert (await _set_role(client, t, t.admin, t.member, "owner")).status_code == 403


async def test_an_admin_cannot_demote_a_fellow_admin(client: AsyncClient) -> None:
    """Otherwise one compromised admin strips every other admin."""
    t = await _team(client)
    assert (await _set_role(client, t, t.admin, t.admin2, "member")).status_code == 403


async def test_an_admin_can_still_manage_the_ranks_below(client: AsyncClient) -> None:
    t = await _team(client)
    assert (await _set_role(client, t, t.admin, t.member, "admin")).status_code == 200


async def test_anyone_can_lower_their_own_role(client: AsyncClient) -> None:
    t = await _team(client)
    assert (await _set_role(client, t, t.admin, t.admin, "member")).status_code == 200


async def test_owners_manage_everyone_and_the_last_owner_rule_holds(client: AsyncClient) -> None:
    t = await _team(client)
    assert (await _set_role(client, t, t.owner, t.admin, "owner")).status_code == 200
    # Two owners now: the original may step down, or be stepped down.
    assert (await _set_role(client, t, t.admin, t.owner, "admin")).status_code == 200
    # ...but the last remaining owner cannot be removed, even by themselves.
    last = await _set_role(client, t, t.admin, t.admin, "member")
    assert last.status_code == 400


async def test_a_refusal_is_audited_nowhere_but_a_change_is(client: AsyncClient) -> None:
    t = await _team(client)
    await _set_role(client, t, t.admin, t.admin, "owner")  # refused
    await _set_role(client, t, t.admin, t.member, "admin")  # allowed
    log = (await client.get(f"/api/v1/orgs/{t.org_id}/audit-log", headers=t.owner.headers)).json()[
        "items"
    ]
    changes = [e for e in log if e["action"] == "org.member.role_change"]
    assert len(changes) == 1
    assert changes[0]["meta"] == {"from": "member", "to": "admin"}


# ── conversations belong to whoever started them ─────────────


async def _conversation_of(client: AsyncClient, team: Team, creator: Actor) -> str:
    a = await client.post("/api/v1/assistants", json={"name": "Shared"}, headers=team.h(team.owner))
    assert a.status_code == 201, a.text
    c = await client.post(
        f"/api/v1/assistants/{a.json()['id']}/conversations", json={}, headers=team.h(creator)
    )
    assert c.status_code == 201, c.text
    return str(c.json()["id"])


async def test_a_member_cannot_touch_a_teammates_conversation(client: AsyncClient) -> None:
    t = await _team(client)
    cid = await _conversation_of(client, t, t.member)
    other = t.h(t.member2)
    rename = await client.patch(f"/api/v1/conversations/{cid}", json={"title": "x"}, headers=other)
    archive = await client.delete(f"/api/v1/conversations/{cid}", headers=other)
    post = await client.post(
        f"/api/v1/conversations/{cid}/messages", json={"text": "hi"}, headers=other
    )
    assert (rename.status_code, archive.status_code, post.status_code) == (403, 403, 403)


async def test_only_the_creator_can(client: AsyncClient) -> None:
    t = await _team(client)
    cid = await _conversation_of(client, t, t.member)
    mine = await client.patch(
        f"/api/v1/conversations/{cid}", json={"title": "Mine"}, headers=t.h(t.member)
    )
    theirs = await client.patch(
        f"/api/v1/conversations/{cid}", json={"title": "Reviewed"}, headers=t.h(t.admin)
    )
    assert (mine.status_code, theirs.status_code) == (200, 403)


async def test_admins_read_a_teammates_conversation_but_cannot_act_in_it(
    client: AsyncClient,
) -> None:
    """Phase 7a.4: a message runs the agent as the conversation's owner (their
    memory, and soon their identity), so an admin posting into it would act
    in their name. Reading stays open to admins; acting does not."""
    t = await _team(client)
    cid = await _conversation_of(client, t, t.member)
    url = f"/api/v1/conversations/{cid}"
    for admin in (t.admin, t.owner):
        h = t.h(admin)
        for read in (url, f"{url}/messages", f"{url}/runs", f"{url}/approvals", f"{url}/turn"):
            assert (await client.get(read, headers=h)).status_code == 200, read
        acts = [
            await client.post(f"{url}/messages", json={"text": "hi"}, headers=h),
            await client.post(f"{url}:interrupt", headers=h),
            await client.patch(url, json={"title": "Reviewed"}, headers=h),
            await client.delete(url, headers=h),
        ]
        assert [r.status_code for r in acts] == [403] * 4
        assert {r.json()["error"]["code"] for r in acts} == {"not_conversation_owner"}
    after = (await client.get(url, headers=t.h(t.member))).json()
    assert after["status"] != "archived" and after["title"] != "Reviewed"
    assert after["messages"] == [], "no admin message reached it"


async def test_a_conversation_says_who_started_it(client: AsyncClient) -> None:
    """The web app shows a read-only view to everyone else (Phase 7a.4)."""
    t = await _team(client)
    cid = await _conversation_of(client, t, t.member)
    got = (await client.get(f"/api/v1/conversations/{cid}", headers=t.h(t.admin))).json()
    assert got["created_by"] == t.member.user_id


async def test_reading_a_teammates_conversation_is_unchanged(client: AsyncClient) -> None:
    """Visibility within the org is not what was broken; only mutation was."""
    t = await _team(client)
    cid = await _conversation_of(client, t, t.member)
    got = await client.get(f"/api/v1/conversations/{cid}", headers=t.h(t.member2))
    assert got.status_code == 200


async def test_rename_and_archive_are_audited(client: AsyncClient) -> None:
    t = await _team(client)
    cid = await _conversation_of(client, t, t.member)
    await client.patch(f"/api/v1/conversations/{cid}", json={"title": "Q3"}, headers=t.h(t.member))
    await client.delete(f"/api/v1/conversations/{cid}", headers=t.h(t.member))
    log = (await client.get(f"/api/v1/orgs/{t.org_id}/audit-log", headers=t.owner.headers)).json()[
        "items"
    ]
    actions = {e["action"] for e in log if e.get("target_id") == cid}
    assert {"conversation.rename", "conversation.archive"} <= actions
