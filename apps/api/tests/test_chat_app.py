"""The Chat app (Phase 8.6): answers that survive a dropped connection,
messages never lost, failures in an employee's words, the offline page's
steps, and signing in on another device with a code.

A dropped stream is picked up where it left off: each event has an id,
and a quiet turn sends keep-alives so a VPN or mobile network doesn't
close the connection meanwhile. The page knows whether its message was
kept (`accepted`), so a failure before that gives the text back and one
after it doesn't ask twice.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.agent.events import ErrorEvent
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.audit_log import AuditLog
from app.models.conversation import Message, MessageRole
from app.models.user_token import TokenPurpose, UserToken
from app.services import account, chat, chat_view, turns
from httpx import AsyncClient
from sqlalchemy import select, update
from tests.versions import go_live

from test_budgets import _assistant, _put, _spend  # type: ignore[import-not-found]
from test_load_tuning import _drain  # type: ignore[import-not-found]
from test_orgs import _register  # type: ignore[import-not-found]
from test_turns import _conversation, _events, _finish  # type: ignore[import-not-found]
from test_two_factor import _turn_on  # type: ignore[import-not-found]

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture(autouse=True)
def _fresh_turns() -> Any:
    turns._memory = turns.MemoryLog()
    yield
    turns._memory = turns.MemoryLog()


# ── answers that survive a dropped connection ────────────────


async def test_a_quiet_turn_sends_keep_alives_with_no_id(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long tool call or a wait for an admin sent nothing for minutes,
    and networks close a connection that idle."""
    cid = await _conversation(client, org_headers)
    monkeypatch.setattr(turns, "BLOCK_S", 0.05)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 400)
    turn_id = await turns.start(uuid.UUID(cid), "hello there")
    seen: list[tuple[str, str]] = []
    async for item in turns.follow(cid, turn_id):
        seen.append(item)
        if item[0] == "":
            break
    assert seen[-1] == ("", turns.KEEPALIVE)
    assert turns.KEEPALIVE.startswith(":"), "an SSE comment: pages ignore it"
    await _finish()


async def test_the_stream_carries_keep_alives_to_the_page(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = await _conversation(client, org_headers)
    monkeypatch.setattr(turns, "BLOCK_S", 0.05)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 300)
    r = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hello"}, headers=org_headers
    )
    frames = r.text.split("\n\n")
    pings = [f for f in frames if f.startswith(": ping")]
    assert pings, "a quiet turn says it is alive"
    # With no id: a page watching again resumes from a real event.
    assert all("id: " not in f for f in pings)
    assert _events(r.text)[-1]["type"] == "done"
    await _finish()


async def test_watching_again_from_the_last_id_misses_nothing_and_repeats_nothing(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = await _conversation(client, org_headers)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 200)
    turn_id = await turns.start(uuid.UUID(cid), "hello there")
    # The connection drops after three events.
    first: list[tuple[str, str]] = []
    stream = turns.follow(cid, turn_id)
    async for item in stream:
        if item[0]:
            first.append(item)
        if len(first) == 3:
            break
    await stream.aclose()
    # Picked up from the last id it saw.
    rest = [item async for item in turns.follow(cid, turn_id, after=first[-1][0]) if item[0]]
    everything = [item async for item in turns.follow(cid, turn_id) if item[0]]
    assert first + rest == everything
    await _finish()


async def test_the_stream_says_when_the_message_is_kept(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    cid = await _conversation(client, org_headers)
    r = await client.post(
        f"{API}/conversations/{cid}/messages", json={"text": "hello"}, headers=org_headers
    )
    events = _events(r.text)
    assert events[0]["type"] == "accepted"
    await _finish()
    async with get_sessionmaker()() as s:
        mine = await s.get(Message, uuid.UUID(events[0]["message_id"]))
    assert mine is not None and mine.role is MessageRole.user and mine.content == "hello"


async def test_a_refusal_before_the_message_is_kept_has_no_accepted_event(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """Over budget: the message isn't kept, so the page gives it back."""
    org = org_headers["X-Org-Id"]
    aid = await _assistant(client, org_headers)
    await _put(client, org_headers, f"{API}/orgs/{org}/budgets", daily_usd=1)
    await _spend(org, 1.0)
    # Live: a preview spends from the builder budget instead (Phase 9.7).
    await go_live(client, aid, org_headers)
    conv = await client.post(f"{API}/assistants/{aid}/conversations", json={}, headers=org_headers)
    r = await client.post(
        f"{API}/conversations/{conv.json()['id']}/messages",
        json={"text": "hello"},
        headers=org_headers,
    )
    events = _events(r.text)
    assert [e["type"] for e in events] == ["error"]
    # When it starts again, for the page to say in the reader's own time.
    resets = datetime.fromisoformat(events[0]["resets_at"])
    assert resets > datetime.now(UTC) and (resets.hour, resets.minute) == (0, 0)
    await _finish()


async def test_busy_is_worth_trying_again_and_says_when(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Peaks are when it happens; it used to say trying again wouldn't help."""
    first = uuid.UUID(await _conversation(client, org_headers))
    second = uuid.UUID(await _conversation(client, org_headers))
    monkeypatch.setattr(settings, "agent_max_concurrency", 1)
    monkeypatch.setattr(settings, "agent_fake_delay_ms", 400)
    monkeypatch.setattr(settings, "agent_queue_wait_s", 0.05)
    maker = get_sessionmaker()
    async with maker() as a, maker() as b:
        one = asyncio.create_task(_drain(chat.run_message(a, conversation_id=first, text="hi")))
        await asyncio.sleep(0.05)
        refused = await _drain(chat.run_message(b, conversation_id=second, text="hi"))
        (busy,) = [e for e in refused if isinstance(e, ErrorEvent)]
        assert (busy.code, busy.retryable, busy.retry_after_s) == ("busy", True, chat.BUSY_RETRY_S)
        assert not any(e.type == "accepted" for e in refused)
        await one


# ── failures in an employee's words ──────────────────────────


@pytest.mark.parametrize(
    ("data", "words"),
    [
        (
            {"code": "context_too_long", "message": "lower the summary threshold (Panels)"},
            "Start a new conversation",
        ),
        ({"code": "billing", "message": "check its billing"}, "problem on the server"),
        ({"code": "busy", "message": "Too many conversations"}, "Try again in a few seconds"),
        (
            {"code": "budget_exceeded", "message": "of $1.00 is used up", "resets_at": "x"},
            "reached its usage limit",
        ),
        ({"code": "budget_exceeded", "message": "spend limit"}, "Start a new conversation"),
        ({"code": "internal_error", "message": "check the server log"}, "Try again in a moment"),
    ],
)
def test_an_employee_reads_failures_without_money_or_panels(
    data: dict[str, Any], words: str
) -> None:
    shaped = chat_view.event({"type": "error", "retryable": False, **data})
    assert shaped is not None and words in shaped["message"]
    for builders_only in ("$", "Panels", "server log", "billing"):
        assert builders_only not in shaped["message"]
    # What the page needs to act on stays.
    assert shaped["code"] == data["code"] and shaped.get("resets_at") == data.get("resets_at")


def test_an_unlisted_failure_keeps_its_own_words_and_accepted_passes() -> None:
    shaped = chat_view.event({"type": "error", "code": "turn_lost", "message": "Send it again."})
    assert shaped is not None and shaped["message"] == "Send it again."
    accepted = {"type": "accepted", "message_id": "m1"}
    assert chat_view.event(accepted) == accepted
    assert chat_view.frame(turns.KEEPALIVE) == turns.KEEPALIVE


# ── the offline page's steps ─────────────────────────────────


async def test_the_owner_writes_the_offline_steps_and_chat_hands_them_out(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    org = org_headers["X-Org-Id"]
    steps = "Open Tailscale and switch it on.\nOr join the office Wi-Fi."
    r = await client.patch(
        f"{API}/orgs/{org}/settings", json={"offline_help": f"  {steps}  "}, headers=org_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["offline_help"] == steps
    got = await client.get(f"{API}/chat/help", headers=org_headers)
    assert got.status_code == 200
    assert got.json()["offline_help"] == steps and got.json()["organisation"]
    async with get_sessionmaker()() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == "org.settings.update"))
    assert row is not None and "offline_help" in row.meta["changed"]
    too_long = await client.patch(
        f"{API}/orgs/{org}/settings", json={"offline_help": "x" * 1001}, headers=org_headers
    )
    assert too_long.status_code == 422


# ── signing in on another device ─────────────────────────────


async def _redeem(client: AsyncClient, code: str) -> Any:
    return await client.post(f"{API}/auth/device-code/redeem", json={"code": code})


async def test_a_code_from_a_signed_in_device_signs_another_in_once(client: AsyncClient) -> None:
    me = await _register(client, "pat@example.com")
    made = await client.post(f"{API}/auth/device-code", headers=me.headers)
    assert made.status_code == 200, made.text
    code = made.json()["code"]
    assert len(code) == account.CODE_LENGTH and set(code) <= set(account.CODE_ALPHABET)
    expires = datetime.fromisoformat(made.json()["expires_at"])
    assert timedelta(minutes=4) < expires - datetime.now(UTC) <= account.DEVICE_CODE_TTL
    async with get_sessionmaker()() as s:
        stored = await s.scalar(
            select(UserToken.expires_at).where(UserToken.purpose == TokenPurpose.device_code)
        )
    assert stored is not None
    assert stored.replace(tzinfo=stored.tzinfo or UTC) - datetime.now(UTC) <= (
        account.DEVICE_CODE_TTL
    )

    # Typed as people type it.
    signed_in = await _redeem(client, f" {code[:4].lower()}-{code[4:]} ")
    assert signed_in.status_code == 200, signed_in.text
    tokens = signed_in.json()
    who = await client.get(
        f"{API}/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert who.json()["user"]["id"] == me.user_id

    again = await _redeem(client, code)
    assert again.status_code == 400 and again.json()["error"]["code"] == "device_code_expired"

    async with get_sessionmaker()() as s:
        actions = [
            (a.action, a.meta)
            for a in await s.scalars(
                select(AuditLog).where(AuditLog.actor_user_id == uuid.UUID(me.user_id))
            )
        ]
    assert ("user.device_code.create", {}) in [(a, m or {}) for a, m in actions]
    assert any(a == "user.login" and (m or {}).get("method") == "device_code" for a, m in actions)


async def test_a_code_stops_working_when_it_expires_or_a_newer_one_is_made(
    client: AsyncClient,
) -> None:
    me = await _register(client, "pat@example.com")
    old = (await client.post(f"{API}/auth/device-code", headers=me.headers)).json()["code"]
    new = (await client.post(f"{API}/auth/device-code", headers=me.headers)).json()["code"]
    assert (await _redeem(client, old)).status_code == 400
    async with get_sessionmaker()() as s:
        await s.execute(
            update(UserToken)
            .where(UserToken.purpose == TokenPurpose.device_code, UserToken.used_at.is_(None))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    assert (await _redeem(client, new)).status_code == 400
    assert (await _redeem(client, "ABCD-EFGH")).status_code == 400


async def test_a_code_is_not_a_second_factor(client: AsyncClient) -> None:
    """With an authenticator, the other device still asks for its code."""
    me = await _register(client, "pat@example.com")
    await _turn_on(client, me.headers, me.user_id)
    code = (await client.post(f"{API}/auth/device-code", headers=me.headers)).json()["code"]
    r = await _redeem(client, code)
    assert r.status_code == 200
    assert r.json()["two_factor_challenge"] and r.json()["access_token"] is None


async def test_making_a_code_needs_a_signed_in_device(client: AsyncClient) -> None:
    assert (await client.post(f"{API}/auth/device-code")).status_code == 401
