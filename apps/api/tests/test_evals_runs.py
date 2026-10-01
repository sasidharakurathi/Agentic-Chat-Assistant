"""Eval suites over the API, and the runner (task 6.1).

Runs are driven by calling `runner.run` directly, as the worker job does:
the queue is replaced by a list. Every turn is the offline FakeDriver's,
which answers with the question it was asked, so the free checks have
something real to pass and fail on.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import anthropic
import pytest
from app import queue
from app.agent import claude_api
from app.agent.caps import CapabilityTool
from app.db.session import get_sessionmaker
from app.evals import runner
from app.models.approval import Approval
from app.models.conversation import Conversation, Run, ToolCall
from app.models.evals import EvalRun, EvalRunStatus
from app.models.usage import UsageEvent, UsageKind
from app.services import chat as chat_svc
from app.services import evals as evals_svc
from httpx import AsyncClient
from sqlalchemy import func, select
from tests import claude_stub

from test_orgs import _register  # type: ignore[import-not-found]

API = "/api/v1"


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """The queue, as a list of the run ids it was given."""
    seen: list[uuid.UUID] = []

    async def enqueue(run_id: uuid.UUID) -> bool:
        seen.append(run_id)
        return True

    monkeypatch.setattr(queue, "enqueue_eval", enqueue)
    return seen


async def _assistant(client: AsyncClient, headers: dict[str, str], **config: Any) -> str:
    a = (await client.post(f"{API}/assistants", json={"name": "Shop"}, headers=headers)).json()
    if config:
        cfg = {**a["draft_config"], **config}
        r = await client.put(f"{API}/assistants/{a['id']}/draft-config", json=cfg, headers=headers)
        assert r.status_code == 200, r.text
    return str(a["id"])


async def _suite(
    client: AsyncClient, headers: dict[str, str], aid: str, cases: list[dict[str, Any]], **cfg: Any
) -> str:
    r = await client.post(
        f"{API}/assistants/{aid}/eval-suites",
        json={"name": "Basics", "config": {"judge": True, **cfg}},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    sid = str(r.json()["id"])
    if cases:
        r = await client.post(
            f"{API}/eval-suites/{sid}/cases:bulk", json={"cases": cases}, headers=headers
        )
        assert r.status_code == 200, r.text
    return sid


async def _start(
    client: AsyncClient, headers: dict[str, str], sid: str, version: str | None = None
) -> str:
    r = await client.post(
        f"{API}/eval-suites/{sid}/runs", json={"assistant_version_id": version}, headers=headers
    )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    return str(r.json()["id"])


async def _finished(client: AsyncClient, headers: dict[str, str], rid: str) -> dict[str, Any]:
    await runner.run(uuid.UUID(rid))
    r = await client.get(f"{API}/eval-runs/{rid}", headers=headers)
    assert r.status_code == 200, r.text
    return dict(r.json())


# ── suites and cases ─────────────────────────────────────────


async def test_a_suite_holds_cases_that_can_be_added_edited_replaced_and_removed(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _suite(
        client,
        org_headers,
        aid,
        [{"input": "First?"}, {"input": "Second?", "expected": {"contains": ["two"]}}],
        pass_score=4,
    )
    suite = (await client.get(f"{API}/eval-suites/{sid}", headers=org_headers)).json()
    assert suite["config"] == {"judge": True, "pass_score": 4}
    assert suite["judge_available"] is False, "the fake driver has no judge"
    assert [(c["position"], c["input"]) for c in suite["cases"]] == [(0, "First?"), (1, "Second?")]
    assert suite["cases"][1]["expected"]["contains"] == ["two"]

    added = await client.post(
        f"{API}/eval-suites/{sid}/cases:bulk",
        json={"cases": [{"input": "Third?"}]},
        headers=org_headers,
    )
    assert [c["position"] for c in added.json()["cases"]] == [0, 1, 2]

    first = suite["cases"][0]["id"]
    edited = await client.put(
        f"{API}/eval-suites/{sid}/cases/{first}",
        json={"input": "First, reworded?", "labels": {"relevant_sources": ["FAQ"]}},
        headers=org_headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["labels"]["relevant_sources"] == ["FAQ"]
    gone = await client.delete(f"{API}/eval-suites/{sid}/cases/{first}", headers=org_headers)
    assert gone.status_code == 200
    again = await client.delete(f"{API}/eval-suites/{sid}/cases/{first}", headers=org_headers)
    assert again.status_code == 404

    replaced = await client.post(
        f"{API}/eval-suites/{sid}/cases:bulk",
        json={"cases": [{"input": "Only one?"}], "mode": "replace"},
        headers=org_headers,
    )
    assert [c["input"] for c in replaced.json()["cases"]] == ["Only one?"]

    renamed = await client.patch(
        f"{API}/eval-suites/{sid}", json={"name": "Renamed"}, headers=org_headers
    )
    assert renamed.json()["name"] == "Renamed"
    assert renamed.json()["config"]["pass_score"] == 4, "config untouched by a rename"
    listing = (await client.get(f"{API}/assistants/{aid}/eval-suites", headers=org_headers)).json()
    assert [(s["name"], s["case_count"], s["last_run"]) for s in listing["suites"]] == [
        ("Renamed", 1, None)
    ]
    assert listing["can_edit"] is True


async def test_a_suite_has_a_size_limit(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evals_svc, "MAX_CASES", 3)
    sid = await _suite(
        client, org_headers, await _assistant(client, org_headers), [{"input": "a"}, {"input": "b"}]
    )
    url = f"{API}/eval-suites/{sid}/cases:bulk"
    two_more = {"cases": [{"input": "c"}, {"input": "d"}]}
    over = await client.post(url, json=two_more, headers=org_headers)
    assert over.status_code == 400 and over.json()["error"]["code"] == "too_many_cases"
    assert "would make 4" in over.json()["error"]["message"]
    # Replacing counts only the new cases.
    ok = await client.post(url, json={**two_more, "mode": "replace"}, headers=org_headers)
    assert ok.status_code == 200 and len(ok.json()["cases"]) == 2


async def test_members_read_evals_but_only_editors_change_or_run_them(
    client: AsyncClient, queued: list[uuid.UUID]
) -> None:
    owner = await _register(client, "eval-owner@example.com")
    member = await _register(client, "eval-member@example.com")
    outsider = await _register(client, "eval-outsider@example.com")
    org = (await client.post(f"{API}/orgs", json={"name": "Evals"}, headers=owner.headers)).json()
    inv = await client.post(
        f"{API}/orgs/{org['id']}/invites",
        json={"email": "eval-member@example.com", "role": "member"},
        headers=owner.headers,
    )
    token = inv.json()["accept_url"].rsplit("/", 1)[-1]
    await client.post(f"{API}/invites/{token}/accept", headers=member.headers)
    o = {**owner.headers, "X-Org-Id": org["id"]}
    m = {**member.headers, "X-Org-Id": org["id"]}

    aid = await _assistant(client, o)
    sid = await _suite(client, o, aid, [{"input": "hello"}])
    rid = await _start(client, o, sid)

    # A member of the org sees everything and changes nothing.
    assert (await client.get(f"{API}/assistants/{aid}/eval-suites", headers=m)).json()[
        "can_edit"
    ] is False
    assert (await client.get(f"{API}/eval-suites/{sid}", headers=m)).status_code == 200
    assert (await client.get(f"{API}/eval-suites/{sid}/runs", headers=m)).status_code == 200
    assert (await client.get(f"{API}/eval-runs/{rid}", headers=m)).status_code == 200
    denied = [
        await client.post(f"{API}/assistants/{aid}/eval-suites", json={"name": "Mine"}, headers=m),
        await client.patch(f"{API}/eval-suites/{sid}", json={"name": "x"}, headers=m),
        await client.delete(f"{API}/eval-suites/{sid}", headers=m),
        await client.post(
            f"{API}/eval-suites/{sid}/cases:bulk", json={"cases": [{"input": "x"}]}, headers=m
        ),
        await client.post(f"{API}/eval-suites/{sid}/runs", json={}, headers=m),
        await client.post(f"{API}/eval-runs/{rid}:cancel", headers=m),
    ]
    assert [r.status_code for r in denied] == [403] * 6, [r.text for r in denied]
    assert len(queued) == 1, "the member's run never reached the queue"

    # Someone outside the org can't tell the suite or the run exists.
    for url in (f"{API}/eval-suites/{sid}", f"{API}/eval-runs/{rid}"):
        assert (await client.get(url, headers=outsider.headers)).status_code == 404


# ── a run ────────────────────────────────────────────────────


CASES = [
    {"input": "hello there", "expected": {"contains": ["hello there"]}},
    {"input": "what is a zebra", "expected": {"contains": ["giraffe"]}},
    {
        "input": "calculate 2 + 3",
        "expected": {"contains": ["5"], "tools": ["calculator"], "not_contains": ["error"]},
    },
]


async def test_a_run_takes_every_case_through_a_real_turn_and_scores_it(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID]
) -> None:
    aid = await _assistant(client, org_headers, tools={"calculator": {"enabled": True}})
    sid = await _suite(client, org_headers, aid, CASES)

    rid = await _start(client, org_headers, sid)
    assert queued == [uuid.UUID(rid)]
    busy = await client.post(f"{API}/eval-suites/{sid}/runs", json={}, headers=org_headers)
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "eval_run_active"

    run = await _finished(client, org_headers, rid)
    assert (run["status"], run["total_cases"], run["done_cases"]) == ("done", 3, 3)
    assert run["error"] is None and run["version_number"] is None
    assert run["started_at"] and run["finished_at"]
    results = run["results"]
    assert [r["passed"] for r in results] == [True, False, True]
    assert [r["position"] for r in results] == [0, 1, 2]
    assert "hello there" in results[0]["output"]
    assert results[1]["scores"]["checks"] == [
        {"kind": "contains", "target": "giraffe", "passed": False}
    ]
    assert results[2]["scores"]["tools"] == ["mcp__caps__calculator"]
    assert [c["passed"] for c in results[2]["scores"]["checks"]] == [True, True, True]
    assert all(r["scores"]["judge"] is None for r in results), "no judge on the fake driver"
    assert all(r["run_id"] and r["conversation_id"] and r["error"] is None for r in results)
    assert run["metrics"]["pass_rate"] == pytest.approx(0.6667)
    assert run["metrics"]["checks"] == {"total": 5, "passed": 4}
    assert (run["metrics"]["judge"], run["metrics"]["retrieval"]) == (None, None)

    # The suite shows its latest run, and can run again now.
    listing = (await client.get(f"{API}/assistants/{aid}/eval-suites", headers=org_headers)).json()
    assert listing["suites"][0]["last_run"]["id"] == rid
    runs = (await client.get(f"{API}/eval-suites/{sid}/runs", headers=org_headers)).json()
    assert [r["id"] for r in runs["runs"]] == [rid]
    await _start(client, org_headers, sid)

    # Running it twice does nothing the second time.
    await runner.run(uuid.UUID(rid))
    assert (
        len((await client.get(f"{API}/eval-runs/{rid}", headers=org_headers)).json()["results"])
        == 3
    )


async def test_a_cases_turn_is_a_hidden_conversation_with_its_own_run(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID]
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _suite(client, org_headers, aid, [{"input": "hello there"}])
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))
    result = run["results"][0]

    listed = await client.get(f"{API}/assistants/{aid}/conversations", headers=org_headers)
    assert listed.json()["items"] == [], "not in the chat list"
    # ...but it is a real conversation: its run details open from the result.
    cid, run_id = result["conversation_id"], result["run_id"]
    detail = await client.get(f"{API}/conversations/{cid}/runs/{run_id}", headers=org_headers)
    assert detail.status_code == 200, detail.text
    conv = (await client.get(f"{API}/conversations/{cid}", headers=org_headers)).json()
    assert conv["title"] == "Eval: hello there", "named up front: no titling call"
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    # Its spend is on the ledger like any turn's.
    async with get_sessionmaker()() as s:
        events = (
            await s.scalars(select(UsageEvent).where(UsageEvent.conversation_id == uuid.UUID(cid)))
        ).all()
    assert [e.kind for e in events] == [UsageKind.llm]

    # Deleting the suite takes its runs and their conversations with it.
    assert (await client.delete(f"{API}/eval-suites/{sid}", headers=org_headers)).status_code == 200
    assert (
        await client.get(f"{API}/eval-runs/{run['id']}", headers=org_headers)
    ).status_code == 404
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(EvalRun)) == 0


async def test_a_run_is_pinned_to_the_version_it_was_started_on(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID]
) -> None:
    aid = await _assistant(client, org_headers)
    v1 = (
        await client.post(
            f"{API}/assistants/{aid}/versions", json={"note": "v1"}, headers=org_headers
        )
    ).json()
    v2 = (
        await client.post(
            f"{API}/assistants/{aid}/versions", json={"note": "v2"}, headers=org_headers
        )
    ).json()
    sid = await _suite(client, org_headers, aid, [{"input": "hello"}])

    async def version_of(version_id: str | None) -> tuple[int | None, int | None]:
        run = await _finished(
            client, org_headers, await _start(client, org_headers, sid, version_id)
        )
        async with get_sessionmaker()() as s:
            turn = await s.get(Run, uuid.UUID(run["results"][0]["run_id"]))
        assert turn is not None
        return run["version_number"], turn.version_number

    # v2 is what's published; the run on v1 still answered as v1.
    assert await version_of(v1["id"]) == (1, 1)
    assert await version_of(v2["id"]) == (2, 2)
    assert await version_of(None) == (None, None), "the draft"

    # A version from another assistant is not this suite's to run.
    other = await _assistant(client, org_headers)
    theirs = (
        await client.post(f"{API}/assistants/{other}/versions", json={}, headers=org_headers)
    ).json()
    wrong = await client.post(
        f"{API}/eval-suites/{sid}/runs",
        json={"assistant_version_id": theirs["id"]},
        headers=org_headers,
    )
    assert wrong.status_code == 404


async def test_a_run_needs_cases_and_a_worker(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    aid = await _assistant(client, org_headers)
    empty = await _suite(client, org_headers, aid, [])
    r = await client.post(f"{API}/eval-suites/{empty}/runs", json={}, headers=org_headers)
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_cases"

    async def down(_run_id: uuid.UUID) -> bool:
        return False

    monkeypatch.setattr(queue, "enqueue_eval", down)
    sid = await _suite(client, org_headers, aid, [{"input": "hello"}])
    r = await client.post(f"{API}/eval-suites/{sid}/runs", json={}, headers=org_headers)
    assert r.status_code == 202
    assert (r.json()["status"], r.json()["error"]) == ("failed", evals_svc.NO_WORKER)
    # It isn't left "queued", which would block the suite for good.
    again = await client.post(f"{API}/eval-suites/{sid}/runs", json={}, headers=org_headers)
    assert again.status_code == 202


# ── nobody to ask ────────────────────────────────────────────


@pytest.fixture
def fake_sql(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The SQL tools, replaced by one that records what it ran."""
    ran: list[Any] = []

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        ran.append(args.get("sql"))
        return {"content": [{"type": "text", "text": "1 row(s) affected"}]}

    def build(_assistant_id: uuid.UUID, _databases: list[Any]) -> list[CapabilityTool]:
        return [
            CapabilityTool(
                name="sql_query",
                description="stub",
                input_schema={"type": "object"},
                handler=handler,
                read_only=False,
            )
        ]

    monkeypatch.setattr("app.agent.options.build_sql_tools", build)
    monkeypatch.setattr("app.agent.options.build_mongo_tools", lambda *_a, **_k: [])
    return ran


async def test_a_case_that_needs_approval_is_declined_not_left_waiting(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID], fake_sql: list[Any]
) -> None:
    aid = await _assistant(client, org_headers)
    conn = await client.post(
        f"{API}/assistants/{aid}/db-connections",
        json={
            "name": "PG",
            "engine": "postgres",
            "host": "db",
            "database": "x",
            "permissions": {"read": True, "write": True, "ddl": True},
        },
        headers=org_headers,
    )
    assert conn.status_code == 201, conn.text
    cfg = (await client.get(f"{API}/assistants/{aid}", headers=org_headers)).json()["draft_config"]
    cfg["databases"] = [{"connection_id": conn.json()["id"], "nl2sql": True, "expose_write": True}]
    await client.put(f"{API}/assistants/{aid}/draft-config", json=cfg, headers=org_headers)
    sid = await _suite(
        client,
        org_headers,
        aid,
        [{"input": "sql: DELETE FROM orders", "expected": {"tools": ["sql_query"]}}],
    )

    run = await _finished(client, org_headers, await _start(client, org_headers, sid))

    assert run["status"] == "done"
    assert fake_sql == [], "the statement never ran"
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(Approval)) == 0, "no pending row"
        call = await s.scalar(select(ToolCall))
    assert call is not None and call.permission == "unattended"
    # The tool was called (the check holds); it just wasn't allowed to run.
    assert run["results"][0]["scores"]["checks"] == [
        {"kind": "tool", "target": "sql_query", "passed": True}
    ]


# ── stopping ─────────────────────────────────────────────────


async def test_cancelling_stops_before_the_next_case_and_keeps_what_ran(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _suite(
        client, org_headers, aid, [{"input": "one"}, {"input": "two"}, {"input": "three"}]
    )

    # Cancelled while still queued: the worker finds nothing to do.
    rid = await _start(client, org_headers, sid)
    cancelled = await client.post(f"{API}/eval-runs/{rid}:cancel", headers=org_headers)
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["finished_at"]
    run = await _finished(client, org_headers, rid)
    assert (run["status"], run["results"]) == ("cancelled", [])

    # Cancelled after its first case.
    rid = await _start(client, org_headers, sid)
    real = runner.run_case

    async def then_cancel(session: Any, run_row: EvalRun, *args: Any) -> Any:
        out = await real(session, run_row, *args)
        async with get_sessionmaker()() as other:
            row = await other.get(EvalRun, run_row.id)
            assert row is not None
            await evals_svc.cancel_run(other, row)
        return out

    monkeypatch.setattr(runner, "run_case", then_cancel)
    run = await _finished(client, org_headers, rid)
    assert run["status"] == "cancelled"
    assert [r["input"] for r in run["results"]] == ["one"]
    assert run["metrics"]["cases"] == 1 and run["done_cases"] == 1

    # A finished run stays finished.
    monkeypatch.setattr(runner, "run_case", real)
    done = await _finished(client, org_headers, await _start(client, org_headers, sid))
    after = await client.post(f"{API}/eval-runs/{done['id']}:cancel", headers=org_headers)
    assert after.json()["status"] == "done"


async def test_a_spent_budget_stops_the_run_and_says_why(
    client: AsyncClient, org_headers: dict[str, str], queued: list[uuid.UUID]
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _suite(client, org_headers, aid, [{"input": "one"}, {"input": "two"}])
    org = org_headers["X-Org-Id"]
    r = await client.put(f"{API}/orgs/{org}/budgets", json={"daily_usd": 1}, headers=org_headers)
    assert r.status_code == 200, r.text
    async with get_sessionmaker()() as s:
        s.add(UsageEvent(org_id=uuid.UUID(org), kind=UsageKind.llm, model="m", cost_usd=2))
        await s.commit()

    run = await _finished(client, org_headers, await _start(client, org_headers, sid))

    assert run["status"] == "failed"
    assert "budget" in run["error"].lower()
    assert len(run["results"]) == 1, "no point asking the second question"
    assert run["results"][0]["passed"] is False and run["results"][0]["error"] == run["error"]
    assert run["metrics"]["errors"] == 1


async def test_one_broken_case_does_not_take_the_run_down(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _suite(
        client,
        org_headers,
        aid,
        [{"input": "boom"}, {"input": "hello", "expected": {"contains": ["hello"]}}],
    )
    real = chat_svc.run_message

    def flaky(session: Any, *, conversation_id: uuid.UUID, text: str, **kw: Any) -> Any:
        if text == "boom":
            raise RuntimeError("driver exploded")
        return real(session, conversation_id=conversation_id, text=text, **kw)

    monkeypatch.setattr(chat_svc, "run_message", flaky)
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))

    assert run["status"] == "done"
    assert [r["passed"] for r in run["results"]] == [False, True]
    assert run["results"][0]["error"] == "The turn failed before it could answer (RuntimeError)."
    assert "driver exploded" not in json.dumps(run), "the raw error stays in the logs"
    assert run["metrics"]["errors"] == 1 and run["metrics"]["passed"] == 1


async def test_a_run_that_is_interrupted_is_not_left_running(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aid = await _assistant(client, org_headers)
    sid = await _suite(client, org_headers, aid, [{"input": "one"}])
    rid = await _start(client, org_headers, sid)

    async def crash(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("worker lost its database")

    monkeypatch.setattr(runner, "run_case", crash)
    run = await _finished(client, org_headers, rid)
    assert run["status"] == "failed"
    assert run["error"] == "The run stopped unexpectedly (RuntimeError)."
    assert run["finished_at"]
    async with get_sessionmaker()() as s:
        row = await s.get(EvalRun, uuid.UUID(rid))
    assert row is not None and not row.status.active
    assert EvalRunStatus.queued.active and EvalRunStatus.running.active


# ── the judge, on the real-model path ────────────────────────


def _verdict(**scores: int) -> Any:
    base = {"groundedness": 5, "correctness": 5, "refusal_appropriateness": 5, **scores}
    body: dict[str, Any] = {k: {"score": v, "rationale": f"{k} ok"} for k, v in base.items()}
    body["citation_validity"] = None
    return claude_stub.message(
        json.dumps(body), tokens_in=1000, tokens_out=100, model="claude-opus-5"
    )


async def test_the_judge_grades_each_answer_and_its_cost_is_recorded(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: True)
    answers = iter(
        [
            _verdict(),
            _verdict(correctness=2),
            claude_stub.message(
                "no", stop_reason="refusal", model="claude-opus-5", tokens_in=1000, tokens_out=100
            ),
            anthropic.APIConnectionError(request=None),  # type: ignore[arg-type]
        ]
    )

    def handler(_request: Any) -> Any:
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    stub = claude_stub.install(monkeypatch, handler)
    aid = await _assistant(client, org_headers)
    sid = await _suite(
        client,
        org_headers,
        aid,
        [{"input": f"hello {n}", "expected": {"contains": ["hello"]}} for n in range(4)],
    )
    suite = (await client.get(f"{API}/eval-suites/{sid}", headers=org_headers)).json()
    assert suite["judge_available"] is True

    run = await _finished(client, org_headers, await _start(client, org_headers, sid))

    good, low, refused, unreachable = (r["scores"]["judge"] for r in run["results"])
    assert good["passed"] is True and good["correctness"]["score"] == 5
    assert low["passed"] is False
    assert refused == {"error": "The judge declined to grade this answer."}
    assert unreachable == {"error": "The judge model couldn't be reached."}
    # Its verdict decides the case; a judge that couldn't grade leaves the
    # checks standing.
    assert [r["passed"] for r in run["results"]] == [True, False, True, True]
    assert run["metrics"]["judge"]["judged"] == 2
    assert run["metrics"]["judge"]["correctness"] == 3.5
    assert run["metrics"]["judge_errors"] == 2
    assert "hello 0" in claude_stub.prompt(stub.requests[0]), "the judge saw the answer"

    # Three calls were billed (the refusal too), on the judge's model.
    call = 1000 * 5 / 1e6 + 100 * 25 / 1e6
    async with get_sessionmaker()() as s:
        judged = (
            await s.scalars(select(UsageEvent).where(UsageEvent.model == "claude-opus-5"))
        ).all()
        turns = await s.scalar(
            select(func.sum(Conversation.cost_usd)).where(Conversation.eval_run_id.is_not(None))
        )
    assert len(judged) == 3
    assert {e.assistant_id for e in judged} == {uuid.UUID(aid)}
    assert run["cost_usd"] == pytest.approx(float(turns or 0) + 3 * call, abs=1e-6)
    assert run["results"][0]["cost_usd"] == pytest.approx(
        run["results"][3]["cost_usd"] + call, abs=1e-6
    )


async def test_a_suite_can_switch_the_judge_off(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(claude_api, "real_model_allowed", lambda: True)
    stub = claude_stub.install(monkeypatch, lambda r: _verdict())
    aid = await _assistant(client, org_headers)
    sid = await _suite(client, org_headers, aid, [{"input": "hello"}], judge=False)
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))
    assert run["results"][0]["scores"]["judge"] is None
    assert stub.requests == []


# ── retrieval labels ─────────────────────────────────────────


async def test_labelled_cases_get_retrieval_scores_when_there_is_a_knowledge_base(
    client: AsyncClient,
    org_headers: dict[str, str],
    queued: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    labelled = {"input": "hello", "labels": {"relevant_sources": ["Refund policy"]}}
    asked: list[str] = []

    async def scored(_session: Any, *, question: str, **_kw: Any) -> dict[str, Any]:
        asked.append(question)
        return {
            "level": "source",
            "k": 8,
            "recall": 1.0,
            "reciprocal_rank": 0.5,
            "ndcg": 0.63,
            "hit": True,
        }

    monkeypatch.setattr(runner, "score_retrieval", scored)

    # No knowledge base on this version: said, not scored as zero.
    aid = await _assistant(client, org_headers)
    sid = await _suite(client, org_headers, aid, [labelled, {"input": "unlabelled"}])
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))
    assert "no knowledge base" in run["results"][0]["scores"]["retrieval"]["error"]
    assert run["results"][1]["scores"]["retrieval"] is None
    assert run["metrics"]["retrieval"] is None and asked == []

    cfg = (await client.get(f"{API}/assistants/{aid}", headers=org_headers)).json()["draft_config"]
    cfg["rag"]["enabled"] = True
    r = await client.put(f"{API}/assistants/{aid}/draft-config", json=cfg, headers=org_headers)
    assert r.status_code == 200, r.text
    run = await _finished(client, org_headers, await _start(client, org_headers, sid))
    assert asked == ["hello"], "only the labelled case"
    assert run["results"][0]["scores"]["retrieval"]["reciprocal_rank"] == 0.5
    assert run["metrics"]["retrieval"] == {
        "cases": 1,
        "recall": 1.0,
        "mrr": 0.5,
        "ndcg": 0.63,
        "missed": 0,
    }


async def test_source_labels_resolve_by_name_or_id_and_report_what_matched_nothing(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    from app.evals.labels import resolve_sources
    from app.models.rag import DataSource, DataSourceType

    aid = uuid.UUID(await _assistant(client, org_headers))
    org = uuid.UUID(org_headers["X-Org-Id"])
    async with get_sessionmaker()() as s:
        policy = DataSource(
            assistant_id=aid, org_id=org, type=DataSourceType.text, name="Refund Policy"
        )
        faq = DataSource(assistant_id=aid, org_id=org, type=DataSourceType.text, name="FAQ")
        s.add_all([policy, faq])
        await s.commit()
        found, unknown = await resolve_sources(
            s, aid, ["REFUND policy", str(faq.id), "Shipping guide"]
        )
        assert found == {str(policy.id), str(faq.id)}
        assert unknown == ["Shipping guide"]
        # Another assistant's sources are not candidates.
        assert await resolve_sources(s, uuid.uuid4(), ["FAQ"]) == (set(), ["FAQ"])
