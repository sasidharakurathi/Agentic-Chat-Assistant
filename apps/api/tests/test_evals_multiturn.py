"""Multi-turn eval cases (Phase 9.8): a case may send follow-up messages
after its question, in the same conversation.

The rule under test: the checks grade the LAST answer, except `tools` and
`expects_approval`, which look at every turn's calls. Cost adds up across
the turns. A turn that fails stops the case as failed, saying which turn.
A case without follow-ups behaves exactly as it did before.

Every turn is the offline FakeDriver's, which echoes the message it was
sent, so which answer was graded is visible in the result.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app import queue, samples
from app.agent import claude_api
from app.db.session import get_sessionmaker
from app.models.conversation import Conversation
from app.models.evals import EvalCase
from app.services import chat as chat_svc
from httpx import AsyncClient
from sqlalchemy import select
from tests import claude_stub

from test_evals_runs import (  # type: ignore[import-not-found]
    _assistant,
    _finished,
    _start,
    _suite,
    _verdict,
)

pytestmark = pytest.mark.anyio

API = "/api/v1"


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    seen: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> bool:
        seen.append(run_id)
        return True

    monkeypatch.setattr(queue, "enqueue_eval", enqueue)
    return seen


async def _cases(client: AsyncClient, headers: dict[str, str], sid: str) -> list[dict[str, Any]]:
    r = await client.get(f"{API}/eval-suites/{sid}", headers=headers)
    assert r.status_code == 200, r.text
    return list(r.json()["cases"])


# ── storing them ─────────────────────────────────────────────


async def test_follow_ups_are_tidied_limited_and_kept_out_of_the_metadata(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _suite(
        client,
        org_headers,
        aid,
        [
            {
                "input": "First?",
                "follow_ups": ["  Second?  ", "", "   ", "Second?"],
                "metadata": {"persona": "employee", "follow_ups": ["smuggled"]},
            },
            {"input": "Alone?", "metadata": {"persona": "x"}},
        ],
    )
    multi, single = await _cases(client, org_headers, sid)
    # Blanks go, repeats stay (asking twice is a real test).
    assert multi["follow_ups"] == ["Second?", "Second?"]
    assert multi["metadata"] == {"persona": "employee"}, "the reserved key never leaks"
    assert (single["follow_ups"], single["metadata"]) == ([], {"persona": "x"})
    async with get_sessionmaker()() as s:
        rows = {c.input: c.meta for c in await s.scalars(select(EvalCase))}
    assert rows["Alone?"] == {"persona": "x"}, "a single-question case stores what it did"
    assert rows["First?"] == {"persona": "employee", "follow_ups": ["Second?", "Second?"]}

    # The explicit field wins over a metadata key of the same name.
    edited = await client.put(
        f"{API}/eval-suites/{sid}/cases/{multi['id']}",
        json={"input": "First?", "metadata": {"follow_ups": ["smuggled"]}},
        headers=org_headers,
    )
    assert edited.status_code == 200, edited.text
    assert (edited.json()["follow_ups"], edited.json()["metadata"]) == ([], {})

    too_many = await client.post(
        f"{API}/eval-suites/{sid}/cases:bulk",
        json={"cases": [{"input": "q", "follow_ups": [f"f{n}" for n in range(6)]}]},
        headers=org_headers,
    )
    assert too_many.status_code == 422
    too_long = await client.post(
        f"{API}/eval-suites/{sid}/cases:bulk",
        json={"cases": [{"input": "q", "follow_ups": ["x" * 8_001]}]},
        headers=org_headers,
    )
    assert too_long.status_code == 422
    five = await client.post(
        f"{API}/eval-suites/{sid}/cases:bulk",
        json={"cases": [{"input": "q", "follow_ups": [f"f{n}" for n in range(5)]}]},
        headers=org_headers,
    )
    assert five.status_code == 200, five.text


async def test_cases_with_follow_ups_survive_an_export_and_reimport(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    """The web's JSON export is the API's case shape; importing it with
    `replace` gives back the same cases."""
    aid = await _assistant(client, org_headers)
    cases = [
        {
            "input": "Reset my password?",
            "follow_ups": ["And if I lost my phone?", "Thanks | anything else?"],
            "expected": {"contains": ["4400"], "tools": ["kb_search"], "cites": True},
            "labels": {"relevant_sources": ["Passwords"]},
            "metadata": {"persona": "employee"},
        },
        {"input": "Just one?"},
    ]
    sid = await _suite(client, org_headers, aid, cases)
    before = await _cases(client, org_headers, sid)
    exported = [
        {k: c[k] for k in ("input", "follow_ups", "expected", "labels", "metadata")} for c in before
    ]
    r = await client.post(
        f"{API}/eval-suites/{sid}/cases:bulk",
        json={"cases": json.loads(json.dumps(exported)), "mode": "replace"},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    after = await _cases(client, org_headers, sid)
    strip = [{k: v for k, v in c.items() if k != "id"} for c in after]
    assert strip == [{k: v for k, v in c.items() if k != "id"} for c in before]
    assert after[0]["follow_ups"] == ["And if I lost my phone?", "Thanks | anything else?"]
    assert after[1]["follow_ups"] == []


# ── running them ─────────────────────────────────────────────


async def test_follow_ups_run_in_order_in_one_conversation_and_the_last_answer_is_graded(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID]
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _suite(
        client,
        org_headers,
        aid,
        [
            {
                "input": "alpha question",
                "follow_ups": ["bravo question", "charlie question"],
                "expected": {"contains": ["charlie question"], "not_contains": ["alpha"]},
            }
        ],
    )
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))

    assert run["status"] == "done"
    (result,) = run["results"]
    assert result["passed"] is True, result
    assert result["error"] is None
    turns = result["turns"]
    assert [t["input"] for t in turns] == ["alpha question", "bravo question", "charlie question"]
    assert "alpha question" in turns[0]["output"] and "bravo question" in turns[1]["output"]
    assert result["output"] == turns[-1]["output"], "the last answer is the graded one"
    assert result["scores"]["turns"] == turns
    assert all(t["error"] is None and t["run_id"] for t in turns)
    assert len({t["run_id"] for t in turns}) == 3, "a run per turn"
    assert result["run_id"] == turns[-1]["run_id"]
    assert result["input"] == "alpha question", "the case is still named by its question"

    # One conversation, the messages in order, and its cost is the case's.
    cid = result["conversation_id"]
    conv = (await client.get(f"{API}/conversations/{cid}", headers=org_headers)).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"] * 3
    assert [m["content"] for m in conv["messages"] if m["role"] == "user"] == [
        "alpha question",
        "bravo question",
        "charlie question",
    ]
    async with get_sessionmaker()() as s:
        spent = await s.scalar(
            select(Conversation.cost_usd).where(Conversation.id == uuid.UUID(cid))
        )
        hidden = await s.scalar(
            select(Conversation.eval_run_id).where(Conversation.id == uuid.UUID(cid))
        )
    assert hidden == uuid.UUID(run["id"])
    assert result["cost_usd"] == pytest.approx(float(spent or 0), abs=1e-6)
    assert result["cost_usd"] == pytest.approx(sum(t["cost_usd"] for t in turns), abs=1e-6)
    assert all(t["cost_usd"] > 0 for t in turns), "each turn's own share"
    assert result["duration_ms"] >= max(t["duration_ms"] for t in turns)


async def test_checks_grade_the_last_answer_not_an_earlier_one(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID]
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _suite(
        client,
        org_headers,
        aid,
        [
            {
                "input": "giraffe facts",
                "follow_ups": ["zebra facts"],
                "expected": {"contains": ["giraffe"]},
            }
        ],
    )
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))
    (result,) = run["results"]
    assert "giraffe" in result["turns"][0]["output"], "the first answer said it"
    assert result["passed"] is False, "but only the last answer counts"
    assert result["scores"]["checks"] == [
        {"kind": "contains", "target": "giraffe", "passed": False}
    ]


async def test_tools_count_across_every_turn(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID]
) -> None:
    aid = await _assistant(client, org_headers, tools={"calculator": {"enabled": True}})
    sid = await _suite(
        client,
        org_headers,
        aid,
        [
            {
                "input": "calculate 6*7",
                "follow_ups": ["thanks, that is all"],
                "expected": {"tools": ["calculator"], "contains": ["that is all"]},
            }
        ],
    )
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))
    (result,) = run["results"]
    assert result["passed"] is True, result
    first, last = result["turns"]
    assert first["tools"] == ["mcp__caps__calculator"]
    assert last["tools"] == [], "the graded turn used no tool itself"
    assert result["scores"]["tools"] == ["mcp__caps__calculator"]
    assert result["scores"]["checks"] == [
        {"kind": "contains", "target": "that is all", "passed": True},
        {"kind": "tool", "target": "calculator", "passed": True},
    ]


async def test_a_follow_up_that_fails_stops_the_case_as_failed(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID]
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _suite(
        client,
        org_headers,
        aid,
        [
            {"input": "hello", "follow_ups": ["fail: overloaded", "never asked"]},
            {"input": "next case", "expected": {"contains": ["next case"]}},
        ],
    )
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))

    assert run["status"] == "done", "one failed case is not a failed run"
    broken, fine = run["results"]
    assert broken["passed"] is False and fine["passed"] is True
    assert broken["error"].startswith("Turn 2 of 3 failed, so the case stopped there: ")
    assert [t["input"] for t in broken["turns"]] == ["hello", "fail: overloaded"]
    assert broken["turns"][0]["error"] is None
    assert broken["turns"][1]["error"] and broken["turns"][1]["error"] in broken["error"]
    conv = (
        await client.get(f"{API}/conversations/{broken['conversation_id']}", headers=org_headers)
    ).json()
    asked = [m["content"] for m in conv["messages"] if m["role"] == "user"]
    assert "never asked" not in asked
    assert run["metrics"]["errors"] == 1


async def test_a_follow_up_that_raises_stops_the_case_too(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = chat_svc.run_message
    sent: list[str] = []

    def flaky(session: Any, *, conversation_id: uuid.UUID, text: str, **kw: Any) -> Any:
        sent.append(text)
        if text == "boom":
            raise RuntimeError("driver exploded")
        return real(session, conversation_id=conversation_id, text=text, **kw)

    monkeypatch.setattr(chat_svc, "run_message", flaky)
    aid = await _assistant(client, org_headers)
    sid = await _suite(
        client, org_headers, aid, [{"input": "hello", "follow_ups": ["boom", "after"]}]
    )
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))
    (result,) = run["results"]
    assert sent == ["hello", "boom"]
    assert result["passed"] is False
    assert result["error"] == (
        "Turn 2 of 3 failed, so the case stopped there: "
        "The turn failed before it could answer (RuntimeError)."
    )
    assert "driver exploded" not in json.dumps(run)


async def test_a_single_question_case_is_scored_exactly_as_before(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID]
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _suite(
        client,
        org_headers,
        aid,
        [
            {"input": "hello there", "expected": {"contains": ["hello"]}},
            {"input": "fail: overloaded"},
        ],
    )
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))
    ok, broken = run["results"]
    assert ok["passed"] is True and ok["turns"] == []
    assert set(ok["scores"]) == {"checks", "judge", "retrieval", "tools", "citations"}
    assert "turns" not in broken["scores"]
    # The error is the turn's own words, with no "Turn 1 of 1" in front.
    assert broken["error"] and not broken["error"].startswith("Turn ")
    conv = (
        await client.get(f"{API}/conversations/{ok['conversation_id']}", headers=org_headers)
    ).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]


async def test_the_judge_grades_the_last_question_with_the_turns_before_it(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: True)
    stub = claude_stub.install(monkeypatch, lambda _r: _verdict())
    aid = await _assistant(client, org_headers)
    sid = await _suite(
        client,
        org_headers,
        aid,
        [{"input": "how do I reset it?", "follow_ups": ["and without my phone?"]}],
    )
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))
    (result,) = run["results"]
    assert result["scores"]["judge"]["passed"] is True
    assert len(stub.requests) == 1, "one verdict per case, not per turn"
    prompt = claude_stub.prompt(stub.requests[0])
    assert "Earlier in this conversation:" in prompt
    assert "User: how do I reset it?" in prompt
    assert "The question being graded: and without my phone?" in prompt


def test_the_it_helpdesk_sample_has_a_multi_turn_case() -> None:
    """Its suite (run on the free driver by test_samples.py) shows the
    feature: a password question, then "and if I lost my phone?"."""
    sample = samples.get("it-helpdesk")
    assert sample is not None and sample.eval_suite is not None
    multi = [c for c in sample.eval_suite.cases if c.follow_ups]
    assert len(multi) == 1
    assert multi[0].expected.contains == ["4400"]
