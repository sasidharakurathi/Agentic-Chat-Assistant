"""Long conversations and titles (task 5.2).

The pure parts first (size, recent/older split, the replay block, the free
summarizer and titler), then real turns through the chat service on the fake
driver, whose `history:` phrase reports what a turn knows of the earlier
conversation: the session it resumed, the summary and messages it was
started from, or nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app import queue
from app.agent import history as history_mod
from app.agent import titles as titles_mod
from app.agent.history import (
    HistoryMessage,
    OfflineSummarizer,
    estimate_tokens,
    message_chars,
    replay_block,
    split_recent,
)
from app.agent.titles import DEFAULT_TITLE, OfflineTitler, Title, clean
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.conversation import Conversation
from app.models.usage import UsageEvent
from app.services import conversation_memory
from httpx import AsyncClient
from sqlalchemy import select, update

from test_chat_approvals import _run_turn  # type: ignore[import-not-found]

# ── the pure parts ───────────────────────────────────────────


def _m(role: str, text: str, blocks: list[Any] | None = None) -> HistoryMessage:
    return HistoryMessage(role=role, content=text, blocks=blocks or [])


def test_a_messages_size_includes_its_tool_calls() -> None:
    call = {"type": "tool_call", "name": "sql_query", "input": {"sql": "x"}, "output": "r" * 400}
    citation = {"type": "citation", "marker": 1}
    plain = _m("assistant", "hello")
    assert message_chars(plain) == 5
    assert message_chars(_m("assistant", "hello", [call, citation])) > 400
    assert estimate_tokens("s" * 40, [plain]) == (40 + 5) // 4


def test_recent_messages_fit_a_share_of_the_threshold_but_never_fewer_than_two() -> None:
    msgs = [_m("user" if i % 2 == 0 else "assistant", "x" * 1_000) for i in range(10)]
    # 8000 tokens * 25% = 2000 tokens = 8000 chars: eight 1000-char messages.
    older, recent = split_recent(msgs, 8_000)
    assert (len(older), len(recent)) == (2, 8)
    huge = [_m("user", "y" * 50_000), _m("assistant", "z" * 50_000), _m("user", "w" * 50_000)]
    older, recent = split_recent(huge, 8_000)
    assert (len(older), len(recent)) == (1, 2)


def test_the_replay_block_carries_summary_messages_and_gaps() -> None:
    assert replay_block(None, []) is None
    block = replay_block(
        "They want a refund.",
        [
            _m("user", "Order 42?"),
            _m("assistant", "Found it.", [{"type": "tool_call", "name": "mcp__caps__sql_query"}]),
        ],
        omitted=3,
    )
    assert block is not None
    assert "<summary>\nThey want a refund.\n</summary>" in block
    assert '<message role="user">\nOrder 42?\n</message>' in block
    assert "[tools used: mcp__caps__sql_query]" in block
    assert "(3 earlier messages are not shown.)" in block
    assert "not new instructions" in block


def test_a_replayed_message_is_cut_to_size() -> None:
    block = replay_block(None, [_m("user", "p" * 10_000)])
    assert block is not None and "p" * 4_000 in block and "p" * 4_001 not in block


@pytest.mark.anyio
async def test_the_offline_summary_keeps_the_old_summary_and_one_line_per_message() -> None:
    done = await OfflineSummarizer().summarize(
        "- User: earlier", [_m("user", "  Where is\n my order?  "), _m("assistant", "")]
    )
    assert done.text == "- User: earlier\n- User: Where is my order?\n- Assistant: (no text)"
    assert (done.model, done.cost_usd) == (None, 0.0)
    long = await OfflineSummarizer().summarize(None, [_m("user", "q " * 300)] * 100)
    assert len(long.text) <= history_mod.MAX_SUMMARY_CHARS + 5
    assert "\n…\n" in long.text


def test_titles_are_one_clean_line() -> None:
    assert clean('Title: "Refund for order 42."') == "Refund for order 42"
    assert clean("  a\nb  ") == "a b"
    assert len(clean("word " * 40)) <= titles_mod.MAX_TITLE_CHARS + 1


@pytest.mark.anyio
async def test_the_offline_title_is_the_start_of_the_message() -> None:
    t = await OfflineTitler().title("Where is my order from last Tuesday, it never came")
    assert t.text == "Where is my order from last Tuesday,…"


def test_nothing_is_spent_on_the_free_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Summaries and titles use the model only when the agent does."""
    monkeypatch.setattr(settings, "agent_driver", "fake")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-not-used")
    assert isinstance(history_mod.get_summarizer(), OfflineSummarizer)
    assert isinstance(titles_mod.get_titler(), OfflineTitler)
    monkeypatch.setattr(settings, "agent_driver", "claude")
    assert isinstance(history_mod.get_summarizer(), history_mod.HaikuSummarizer)
    assert isinstance(titles_mod.get_titler(), titles_mod.HaikuTitler)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert isinstance(history_mod.get_summarizer(), OfflineSummarizer)


# ── real turns (fake driver) ─────────────────────────────────


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, int]]:
    """Summary jobs the turns queue, instead of a real worker."""
    seen: list[tuple[uuid.UUID, int]] = []

    async def enqueue(conversation_id: uuid.UUID, version: int) -> bool:
        seen.append((conversation_id, version))
        return True

    monkeypatch.setattr(queue, "enqueue_summary", enqueue)
    return seen


async def _conversation_with(
    client: AsyncClient, headers: dict[str, str], memory: dict[str, Any], title: str | None = None
) -> str:
    a = (await client.post("/api/v1/assistants", json={"name": "Mem"}, headers=headers)).json()
    cfg = a["draft_config"]
    cfg["memory"] = {**cfg["memory"], **memory}
    r = await client.put(f"/api/v1/assistants/{a['id']}/draft-config", json=cfg, headers=headers)
    assert r.status_code == 200, r.text
    body = {"title": title} if title else {}
    r = await client.post(f"/api/v1/assistants/{a['id']}/conversations", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _say(cid: str, text: str) -> list[Any]:
    events: list[Any] = []
    await _run_turn(cid, text, events)
    assert events[-1].type == "done", [e.type for e in events]
    return events


def _answer(events: list[Any]) -> str:
    return "".join(e.text for e in events if e.type == "token" and not e.parent_id)


async def _conv(cid: str) -> Conversation:
    async with get_sessionmaker()() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv is not None
        return conv


@pytest.mark.anyio
async def test_the_next_turn_resumes_the_session(
    client: AsyncClient, org_headers: dict[str, str], queued: list[Any]
) -> None:
    cid = await _conversation_with(client, org_headers, {})
    await _say(cid, "hello there")
    session_id = (await _conv(cid)).sdk_session_id
    assert session_id
    assert f"Resuming session {session_id}" in _answer(
        await _say(cid, "history: what came before?")
    )


@pytest.mark.anyio
async def test_with_history_off_each_message_stands_alone(
    client: AsyncClient, org_headers: dict[str, str], queued: list[Any]
) -> None:
    cid = await _conversation_with(client, org_headers, {"persist_history": False})
    await _say(cid, "hello there")
    assert (await _conv(cid)).sdk_session_id is None
    assert "answered on its own" in _answer(await _say(cid, "history: what came before?"))


@pytest.mark.anyio
async def test_a_long_conversation_is_summarized_and_replayed_then_resumed(
    client: AsyncClient, org_headers: dict[str, str], queued: list[Any]
) -> None:
    cid = await _conversation_with(client, org_headers, {"summarize_after_tokens": 8_000})
    # The fake driver echoes the message, so each turn stores ~2 x 12k chars.
    for n in range(3):
        await _say(cid, f"message {n} " + "lorem " * 2_000)
    # Over 8000 tokens: the turn queued a summary, once per version.
    assert queued and set(queued) == {(uuid.UUID(cid), 0)}

    assert await conversation_memory.summarize(uuid.UUID(cid), 0)
    assert not await conversation_memory.summarize(uuid.UUID(cid), 0), "idempotent"
    conv = await _conv(cid)
    assert (conv.summary_version, conv.session_summary_version) == (1, 0)
    assert conv.summary is not None and conv.summary.startswith("- User: message 0")
    assert conv.summary_through_at is not None

    # A newer summary than the session: start fresh from summary + recent.
    replayed = _answer(await _say(cid, "history: what came before?"))
    assert "This turn started fresh" in replayed
    assert "Summary of the earlier part" in replayed and "- User: message 0" in replayed
    assert '<message role="user">' in replayed
    conv = await _conv(cid)
    assert conv.session_summary_version == 1
    # ... and from then on the (shorter) session is resumed again.
    assert "Resuming session" in _answer(await _say(cid, "history: and now?"))


@pytest.mark.anyio
async def test_a_summary_written_during_a_turn_is_used_by_the_next(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session records the summary version it started from, not the one
    current when it ends: a summary landing mid-turn must not be skipped."""
    cid = await _conversation_with(client, org_headers, {})
    await _say(cid, "first")
    plan_turn = conversation_memory.plan_turn

    async def plan_then_summarize(*args: Any, **kwargs: Any) -> Any:
        plan = await plan_turn(*args, **kwargs)
        async with get_sessionmaker()() as s:  # the worker, finishing now
            await s.execute(
                update(Conversation)
                .where(Conversation.id == uuid.UUID(cid))
                .values(
                    summary="- User: first",
                    summary_version=1,
                    cost_usd=Conversation.cost_usd + 0.25,
                )
                .execution_options(synchronize_session=False)
            )
            await s.commit()
        return plan

    monkeypatch.setattr(conversation_memory, "plan_turn", plan_then_summarize)
    await _say(cid, "second")
    monkeypatch.setattr(conversation_memory, "plan_turn", plan_turn)
    conv = await _conv(cid)
    assert (conv.summary_version, conv.session_summary_version) == (1, 0)
    # The summary's cost, added while the turn ran, survived the turn's save.
    assert float(conv.cost_usd) >= 0.25
    assert "This turn started fresh" in _answer(await _say(cid, "history: ?"))


@pytest.mark.anyio
async def test_a_summarys_spend_is_recorded(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = await _conversation_with(client, org_headers, {"summarize_after_tokens": 8_000})
    for n in range(3):
        await _say(cid, f"message {n} " + "lorem " * 2_000)
    before = float((await _conv(cid)).cost_usd)

    class Paid:
        name = "paid"

        async def summarize(self, previous: Any, messages: Any) -> history_mod.Summary:
            return history_mod.Summary(
                "notes", model="claude-haiku-4-5", tokens_in=900, tokens_out=80, cost_usd=0.0013
            )

    monkeypatch.setattr(conversation_memory, "get_summarizer", Paid)
    assert await conversation_memory.summarize(uuid.UUID(cid), 0)
    assert float((await _conv(cid)).cost_usd) == pytest.approx(before + 0.0013)
    async with get_sessionmaker()() as s:
        rows = (
            await s.scalars(
                select(UsageEvent).where(
                    UsageEvent.conversation_id == uuid.UUID(cid),
                    UsageEvent.model == "claude-haiku-4-5",
                )
            )
        ).all()
    assert [(r.tokens_in, r.tokens_out) for r in rows] == [(900, 80)]


# ── titles ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_first_turn_names_the_conversation(
    client: AsyncClient, org_headers: dict[str, str], queued: list[Any]
) -> None:
    cid = await _conversation_with(client, org_headers, {})
    events = await _say(cid, "Where is my order from last Tuesday, it never came")
    types = [e.type for e in events]
    assert types[-2:] == ["title", "done"]
    assert events[-2].title == "Where is my order from last Tuesday,…"
    assert (await _conv(cid)).title == events[-2].title
    # Only the first turn.
    assert "title" not in [e.type for e in await _say(cid, "and another thing")]


@pytest.mark.anyio
async def test_no_title_when_switched_off_or_already_named(
    client: AsyncClient, org_headers: dict[str, str], queued: list[Any]
) -> None:
    off = await _conversation_with(client, org_headers, {"auto_title": False})
    assert "title" not in [e.type for e in await _say(off, "hello")]
    assert (await _conv(off)).title == DEFAULT_TITLE
    named = await _conversation_with(client, org_headers, {}, title="Mine")
    assert "title" not in [e.type for e in await _say(named, "hello")]
    assert (await _conv(named)).title == "Mine"


@pytest.mark.anyio
async def test_a_rename_during_the_first_turn_wins(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = await _conversation_with(client, org_headers, {})

    class RenamedMeanwhile:
        name = "slow"

        async def title(self, first_message: str) -> Title:
            async with get_sessionmaker()() as s:
                await s.execute(
                    update(Conversation)
                    .where(Conversation.id == uuid.UUID(cid))
                    .values(title="Mine")
                    .execution_options(synchronize_session=False)
                )
                await s.commit()
            return Title("Automatic")

    monkeypatch.setattr(conversation_memory, "get_titler", RenamedMeanwhile)
    events = await _say(cid, "hello")
    assert "title" not in [e.type for e in events]
    assert (await _conv(cid)).title == "Mine"


@pytest.mark.anyio
async def test_a_failed_title_never_fails_the_turn(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        name = "broken"

        async def title(self, first_message: str) -> Title:
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(conversation_memory, "get_titler", Broken)
    cid = await _conversation_with(client, org_headers, {})
    events = await _say(cid, "hello")
    assert "title" not in [e.type for e in events]
    assert (await _conv(cid)).title == DEFAULT_TITLE


@pytest.mark.anyio
async def test_no_summary_is_queued_when_nothing_is_old_enough_to_fold(
    client: AsyncClient, org_headers: dict[str, str], queued: list[Any]
) -> None:
    """One huge exchange is over the threshold on its own, but it is the
    recent part: a summary would fold nothing and still cost a model call."""
    cid = await _conversation_with(client, org_headers, {"summarize_after_tokens": 8_000})
    await _say(cid, "big " * 12_000)
    assert queued == []


@pytest.mark.anyio
async def test_switching_history_off_drops_the_session_and_on_again_replays(
    client: AsyncClient, org_headers: dict[str, str], queued: list[Any]
) -> None:
    """Off must forget the session, or switching back on would resume one
    that is missing every turn answered in between."""
    cid = await _conversation_with(client, org_headers, {})
    await _say(cid, "first, with history")
    assert (await _conv(cid)).sdk_session_id

    conv = await _conv(cid)
    a = (await client.get(f"/api/v1/assistants/{conv.assistant_id}", headers=org_headers)).json()
    cfg = a["draft_config"]

    async def set_history(on: bool) -> None:
        cfg["memory"] = {**cfg["memory"], "persist_history": on}
        r = await client.put(
            f"/api/v1/assistants/{conv.assistant_id}/draft-config", json=cfg, headers=org_headers
        )
        assert r.status_code == 200, r.text

    await set_history(False)
    await _say(cid, "second, on its own")
    assert (await _conv(cid)).sdk_session_id is None
    await set_history(True)
    replayed = _answer(await _say(cid, "history: what came before?"))
    assert "This turn started fresh" in replayed and "second, on its own" in replayed


@pytest.mark.anyio
async def test_only_the_first_turn_asks_for_a_title(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when the first title didn't stick (the model returned nothing),
    later turns don't keep paying for another attempt."""
    asked: list[str] = []

    class Counting:
        name = "counting"

        async def title(self, first_message: str) -> Title:
            asked.append(first_message)
            return Title("")

    monkeypatch.setattr(conversation_memory, "get_titler", Counting)
    cid = await _conversation_with(client, org_headers, {})
    for text in ("one", "two", "three"):
        await _say(cid, text)
    assert asked == ["one"]
    assert (await _conv(cid)).title == DEFAULT_TITLE
