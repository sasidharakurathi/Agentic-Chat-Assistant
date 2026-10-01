"""Detached turns (QOS-01).

A turn runs on the server and is only watched by requests. These hold the
promises that makes: leaving does not end a turn; a page can find it and
watch it again, from the start or from where it left off; one runs per
conversation and many conversations can run at once; Stop and approvals
work with nobody watching; and a turn can only be watched through the
conversation it belongs to.

The HTTP test client runs a request to its end before handing over the
response (see `test_chat_approvals.py`), so "mid-turn" is tested a level
down, on `turns.start` and `turns.follow`, and the routes are tested on
what they answer.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.approval import Approval, ApprovalStatus
from app.models.conversation import Message, MessageRole, Run, RunStatus
from app.services import turns
from httpx import AsyncClient
from sqlalchemy import select

from test_chat_approvals import (  # type: ignore[import-not-found]
    _assistant_with_db,
    fake_sql,  # noqa: F401 - a fixture, used by name
)

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture(autouse=True)
def _fresh_turns() -> Any:
    turns._memory = turns.MemoryLog()
    yield
    turns._memory = turns.MemoryLog()


async def _conversation(
    client: AsyncClient, headers: dict[str, str], aid: str | None = None
) -> str:
    if aid is None:
        a = (await client.post(f"{API}/assistants", json={"name": "Shop"}, headers=headers)).json()
        aid = a["id"]
    conv = await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=headers)
    return str(conv.json()["id"])


async def _finish(wait_s: float = 15.0) -> None:
    """Wait for every turn this process is running."""
    if turns._tasks:
        await asyncio.wait_for(asyncio.gather(*list(turns._tasks)), wait_s)


async def _runs(cid: str) -> list[Run]:
    async with get_sessionmaker()() as s:
        return list(
            (await s.scalars(select(Run).where(Run.conversation_id == uuid.UUID(cid)))).all()
        )


def _events(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _ids(text: str) -> list[str]:
    return [line[4:] for line in text.splitlines() if line.startswith("id: ")]


async def _collect(stream: AsyncIterator[tuple[str, str]], n: int | None = None) -> list[str]:
    seen: list[str] = []
    async for event_id, _frame in stream:
        seen.append(event_id)
        if n is not None and len(seen) >= n:
            break
    return seen


# ── leaving does not end it ──────────────────────────────────


async def test_a_turn_carries_on_when_nobody_watches(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before: closing the stream (a reload, another conversation) ended the
    turn, recorded as aborted, its answer lost."""
    cid = await _conversation(client, org_headers)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 400)
    turn_id = await turns.start(uuid.UUID(cid), "hello there")
    # Watch the first event, then leave.
    first = turns.follow(cid, turn_id)
    assert len(await _collect(first, n=1)) == 1
    await first.aclose()

    await _finish()
    (run,) = await _runs(cid)
    assert run.status is RunStatus.ok
    async with get_sessionmaker()() as s:
        answer = await s.scalar(
            select(Message).where(
                Message.conversation_id == uuid.UUID(cid), Message.role == MessageRole.assistant
            )
        )
    assert answer is not None and "hello there" in answer.content


async def test_sending_still_streams_the_whole_answer_with_event_ids(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    cid = await _conversation(client, org_headers)
    r = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hello"}, headers=org_headers
    )
    assert r.status_code == 200
    turn_id = r.headers["x-turn-id"]
    events = _events(r.text)
    assert events[0]["type"] == "token" and events[-1]["type"] == "done"
    # Every event carries an id, to watch again from where one left off.
    ids = _ids(r.text)
    assert len(ids) == len(events) and len(set(ids)) == len(ids)
    await _finish()
    assert len(turn_id) == 32


# ── finding it and watching it again ─────────────────────────


async def test_a_page_finds_the_running_turn_and_watches_it_from_the_start(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = await _conversation(client, org_headers)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 300)
    turn_id = await turns.start(uuid.UUID(cid), "hello there")

    state = (await client.get(f"{API}/conversations/{cid}/turn", headers=org_headers)).json()
    assert state == {"turn_id": turn_id}
    detail = (await client.get(f"{API}/conversations/{cid}", headers=org_headers)).json()
    assert detail["turn_id"] == turn_id and detail["running"] is True
    listed = (
        await client.get(
            f"{API}/assistants/{detail['assistant_id']}/conversations", headers=org_headers
        )
    ).json()["items"]
    assert [c["running"] for c in listed if c["id"] == cid] == [True]

    # Watching from the start replays what was already said, then follows.
    watched = await client.get(
        f"{API}/conversations/{cid}/turns/{turn_id}/events", headers=org_headers
    )
    assert watched.status_code == 200
    events = _events(watched.text)
    assert events[0]["type"] == "token" and events[-1]["type"] == "done"
    await _finish()

    assert (await client.get(f"{API}/conversations/{cid}/turn", headers=org_headers)).json() == {
        "turn_id": None
    }
    listed = (
        await client.get(
            f"{API}/assistants/{detail['assistant_id']}/conversations", headers=org_headers
        )
    ).json()["items"]
    assert [c["running"] for c in listed if c["id"] == cid] == [False]


async def test_watching_again_picks_up_after_the_last_event_seen(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    cid = await _conversation(client, org_headers)
    sent = await client.post(
        f"{API}/conversations/{cid}/messages",
        json={"text": "one two three four"},
        headers=org_headers,
    )
    turn_id, ids = sent.headers["x-turn-id"], _ids(sent.text)
    await _finish()
    url = f"{API}/conversations/{cid}/turns/{turn_id}/events"

    # A finished turn stays readable for a while: the whole answer again.
    again = await client.get(url, headers=org_headers)
    assert _ids(again.text) == ids
    # From after the second event: the rest only. As a parameter, or as the
    # header a browser's EventSource sends.
    rest = await client.get(url, params={"after": ids[1]}, headers=org_headers)
    assert _ids(rest.text) == ids[2:]
    rest = await client.get(url, headers={**org_headers, "Last-Event-ID": ids[1]})
    assert _ids(rest.text) == ids[2:]


# ── one per conversation, many at once ──────────────────────


async def test_one_turn_per_conversation_and_many_conversations_at_once(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await _conversation(client, org_headers)
    second = await _conversation(client, org_headers)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 400)
    await turns.start(uuid.UUID(first), "hello")
    await turns.start(uuid.UUID(second), "hello")  # another conversation: fine
    assert turns.running_here() == 2

    busy = await client.post(
        f"{API}/conversations/{first}/messages", json={"text": "again"}, headers=org_headers
    )
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "turn_in_progress"
    with pytest.raises(turns.TurnInProgress):
        await turns.start(uuid.UUID(first), "again")

    await _finish()
    assert [r.status for r in await _runs(first)] == [RunStatus.ok]
    assert [r.status for r in await _runs(second)] == [RunStatus.ok]
    # Once it ends, the next one can start.
    await turns.start(uuid.UUID(first), "again")
    await _finish()
    assert len(await _runs(first)) == 2


# ── Stop and approvals, with nobody watching ────────────────


async def test_stop_reaches_a_turn_nobody_is_watching(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = await _conversation(client, org_headers)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 3000)
    await turns.start(uuid.UUID(cid), "one two three four five six")
    await asyncio.sleep(0.3)
    stopped = await client.post(f"{API}/conversations/{cid}:interrupt", headers=org_headers)
    assert stopped.status_code == 202
    await _finish(wait_s=5)
    (run,) = await _runs(cid)
    # Stopped well before its three seconds, and recorded as stopped.
    assert run.status is RunStatus.aborted and run.error == "interrupted"
    assert run.duration_ms < 2500


async def test_an_approval_waits_for_its_decision_with_nobody_watching(
    client: AsyncClient,
    org_headers: dict[str, str],
    fake_sql: dict[str, Any],  # noqa: F811 - the fixture
) -> None:
    aid = await _assistant_with_db(client, org_headers)
    cid = await _conversation(client, org_headers, aid)
    turn_id = await turns.start(uuid.UUID(cid), "sql: DELETE FROM sessions WHERE expired")

    # Nobody attached. The approval is there to be found and decided.
    pending: list[dict[str, Any]] = []
    for _ in range(100):
        pending = (
            await client.get(f"{API}/conversations/{cid}/approvals", headers=org_headers)
        ).json()["items"]
        if pending:
            break
        await asyncio.sleep(0.05)
    assert len(pending) == 1 and fake_sql["ran"] == []
    decided = await client.post(
        f"{API}/approvals/{pending[0]['id']}:resolve",
        json={"decision": "approved"},
        headers=org_headers,
    )
    assert decided.status_code == 200
    await _finish()
    assert fake_sql["ran"] == ["DELETE FROM sessions WHERE expired"]
    # And whoever watches afterwards sees the whole turn, the card included.
    events = _events(
        (
            await client.get(
                f"{API}/conversations/{cid}/turns/{turn_id}/events", headers=org_headers
            )
        ).text
    )
    kinds = [e["type"] for e in events]
    assert "approval_required" in kinds and kinds[-1] == "done"
    async with get_sessionmaker()() as s:
        (row,) = (await s.scalars(select(Approval))).all()
    assert row.status is ApprovalStatus.approved


# ── whose turn it is ─────────────────────────────────────────


async def test_a_turn_is_watched_only_through_its_own_conversation(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    mine = await _conversation(client, org_headers)
    other = await _conversation(client, org_headers)
    sent = await client.post(
        f"{API}/conversations/{mine}/messages", json={"text": "hello"}, headers=org_headers
    )
    turn_id = sent.headers["x-turn-id"]
    await _finish()

    # The right turn id through the wrong conversation: nothing.
    wrong = await client.get(
        f"{API}/conversations/{other}/turns/{turn_id}/events", headers=org_headers
    )
    assert wrong.status_code == 404
    assert wrong.json()["error"]["code"] == "turn_not_found"
    made_up = await client.get(
        f"{API}/conversations/{mine}/turns/{uuid.uuid4().hex}/events", headers=org_headers
    )
    assert made_up.status_code == 404
    bad = await client.get(
        f"{API}/conversations/{mine}/turns/not-an-id/events", headers=org_headers
    )
    assert bad.status_code == 422


async def test_another_orgs_turn_cannot_be_found_or_watched(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    from test_orgs import _register  # type: ignore[import-not-found]

    cid = await _conversation(client, org_headers)
    sent = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hello"}, headers=org_headers
    )
    await _finish()
    actor = await _register(client, "stranger@example.com")
    me = (await client.get(f"{API}/auth/me", headers=actor.headers)).json()
    stranger = {**actor.headers, "X-Org-Id": me["memberships"][0]["org_id"]}
    for url in (
        f"{API}/conversations/{cid}/turn",
        f"{API}/conversations/{cid}/turns/{sent.headers['x-turn-id']}/events",
    ):
        assert (await client.get(url, headers=stranger)).status_code == 404


# ── when the process or Redis goes ──────────────────────────


async def test_a_shutdown_stops_running_turns_and_tells_watchers(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = await _conversation(client, org_headers)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 5000)
    turn_id = await turns.start(uuid.UUID(cid), "one two three four")
    await asyncio.sleep(0.3)
    await turns.shutdown(wait_s=5)
    assert turns.running_here() == 0
    (run,) = await _runs(cid)
    assert run.status is RunStatus.aborted  # recorded, not dropped
    frames = [frame async for _id, frame in turns.follow(cid, turn_id)]
    assert frames[-1] == turns.LOST
    assert await turns.running_turn(cid) is None


async def test_without_its_log_store_a_turn_still_runs_in_memory(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down(turns.MemoryLog):
        async def claim(self, conversation_id: str, turn_id: str) -> bool:
            raise ConnectionError("redis is down")

    monkeypatch.setattr(turns, "_primary", Down)
    cid = await _conversation(client, org_headers)
    turn_id = await turns.start(uuid.UUID(cid), "hello")
    assert turn_id in turns._in_memory
    frames = [frame async for _id, frame in turns.follow(cid, turn_id)]
    assert json.loads(frames[-1][6:])["type"] == "done"
    await _finish()
    assert [r.status for r in await _runs(cid)] == [RunStatus.ok]


async def test_an_event_the_log_cannot_take_does_not_stop_the_turn(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(_cid: str, _turn_id: str, _frame: str) -> None:
        raise ConnectionError("redis went away")

    monkeypatch.setattr(turns._memory, "append", broken)
    cid = await _conversation(client, org_headers)
    await turns.start(uuid.UUID(cid), "hello")
    await _finish()
    assert [r.status for r in await _runs(cid)] == [RunStatus.ok]


def test_the_lost_message_is_an_ordinary_retryable_error() -> None:
    event = json.loads(turns.LOST[6:])
    assert event == {
        "type": "error",
        "code": "turn_lost",
        "message": event["message"],
        "retryable": True,
    }
    assert turns.LOST.startswith("data: ")
