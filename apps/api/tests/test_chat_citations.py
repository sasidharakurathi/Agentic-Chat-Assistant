"""Citations through the real SSE endpoint (task 2.9).

These run in the **unit** tier, which means SQLite — so ``retrieve`` is
monkeypatched rather than called for real. That is not a shortcut: with
``rag.enabled`` the real ``kb_search`` handler opens its own session against
the app's configured ``DATABASE_URL`` and issues pgvector SQL (``embedding
<=> ?``) that SQLite cannot parse. The resulting ``OperationalError`` is
caught by the SSE route's blanket handler and turned into an
``internal_error`` frame on a **200** response — so a test asserting only
"the request succeeded" would pass with the entire feature unexercised.

Every test here therefore asserts a terminal ``done`` and the absence of
``error``, and the real Postgres path is covered separately in
``test_agent_caps_rag.py``.
"""

from __future__ import annotations

import json
import uuid

import pytest
from app.rag.retrieve import RetrievedChunk
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


def _chunk(title: str, content: str, **kw: object) -> RetrievedChunk:
    defaults: dict = {
        "chunk_id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "data_source_id": uuid.uuid4(),
        "title": title,
        "uri": None,
        "content": content,
        "score": 0.9,
        "source_type": "text",
        "page": None,
        "breadcrumb": [],
        "char_start": 100,
        "char_end": 900,
        "ordinal": 0,
    }
    defaults.update(kw)
    return RetrievedChunk(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def fake_kb(monkeypatch: pytest.MonkeyPatch) -> list[list[RetrievedChunk]]:
    """Stand in for retrieval. Each call pops the next batch, so a turn with
    two kb_search calls gets two genuinely different result sets."""
    batches: list[list[RetrievedChunk]] = [
        [
            _chunk("Refund Policy", "Refunds are processed within five business days."),
            _chunk("Shipping", "Orders ship within two days."),
        ],
        [_chunk("Exchange Policy", "Exchanges are accepted for thirty days.")],
    ]
    calls = {"n": 0}

    async def fake_retrieve(*_a: object, **_k: object) -> list[RetrievedChunk]:
        i = min(calls["n"], len(batches) - 1)
        calls["n"] += 1
        return batches[i]

    # caps_rag imports `retrieve` at module scope and calls the bare name, so
    # patching the name in that module is what the handler actually sees.
    monkeypatch.setattr("app.agent.caps_rag.retrieve", fake_retrieve)
    return batches


async def _rag_assistant(
    client: AsyncClient, org_headers: dict[str, str], *, citations: bool = True, budget: float = 0
) -> str:
    a = (
        await client.post("/api/v1/assistants", json={"name": "KB Bot"}, headers=org_headers)
    ).json()
    cfg = a["draft_config"]
    cfg["rag"] = {**cfg["rag"], "enabled": True, "citations": citations}
    if budget:
        cfg["models"]["main"]["max_budget_usd"] = budget
    r = await client.put(
        f"/api/v1/assistants/{a['id']}/draft-config", json=cfg, headers=org_headers
    )
    assert r.status_code == 200, r.text
    return str(a["id"])


async def _conversation(client: AsyncClient, org_headers: dict[str, str], aid: str) -> str:
    r = await client.post(f"/api/v1/assistants/{aid}/conversations", json={}, headers=org_headers)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _send(
    client: AsyncClient, org_headers: dict[str, str], cid: str, text: str
) -> list[dict]:
    events: list[dict] = []
    async with client.stream(
        "POST",
        f"/api/v1/conversations/{cid}/messages",
        json={"text": text},
        headers=org_headers,
    ) as resp:
        assert resp.status_code == 200, resp.text
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _citations(events: list[dict]) -> list[dict]:
    return [e for e in events if e["type"] == "citation"]


async def test_a_kb_turn_emits_citation_events_and_persists_them(
    client: AsyncClient, org_headers: dict[str, str], fake_kb: object
) -> None:
    aid = await _rag_assistant(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    events = await _send(client, org_headers, cid, "please search the kb: refunds?")
    kinds = [e["type"] for e in events]
    # A swallowed internal_error also arrives on a 200, so the terminal `done`
    # is the assertion that actually proves the turn ran.
    assert kinds[-1] == "done"
    assert "error" not in kinds

    cites = _citations(events)
    assert [c["marker"] for c in cites] == [1, 2]
    assert cites[0]["title"] == "Refund Policy"
    assert all(c["chunk_id"] and c["document_id"] for c in cites)

    # Citations arrive after the message is committed, before `done`.
    assert kinds.index("citation") > kinds.index("tool_result")

    detail = (await client.get(f"/api/v1/conversations/{cid}", headers=org_headers)).json()
    assistant_msg = [m for m in detail["messages"] if m["role"] == "assistant"][-1]
    blocks = assistant_msg["blocks"]
    persisted = [b for b in blocks if b.get("type") == "citation"]
    assert [b["marker"] for b in persisted] == [1, 2]
    # Tool calls survive alongside them, now carrying an explicit type.
    assert [b for b in blocks if b.get("type") == "tool_call"]


async def test_markers_continue_across_two_searches_in_one_turn(
    client: AsyncClient, org_headers: dict[str, str], fake_kb: object
) -> None:
    aid = await _rag_assistant(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    events = await _send(client, org_headers, cid, "search twice: refunds and exchanges")
    assert [e["type"] for e in events][-1] == "done"
    markers = [c["marker"] for c in _citations(events)]
    # Second search continues at 3 rather than restarting at 1 — without this
    # the answer's two [1]s would be indistinguishable.
    assert markers == [1, 2, 3]
    titles = [c["title"] for c in _citations(events)]
    assert titles[-1] == "Exchange Policy"


async def test_markers_do_not_restart_on_a_follow_up_turn(
    client: AsyncClient, org_headers: dict[str, str], fake_kb: object
) -> None:
    """The SDK session is resumed, so turn 1's markers are still in the
    model's context. Turn 2 must not reissue them for different chunks."""
    aid = await _rag_assistant(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    first = await _send(client, org_headers, cid, "please search: refunds?")
    assert [c["marker"] for c in _citations(first)] == [1, 2]

    second = await _send(client, org_headers, cid, "please search: exchanges?")
    assert [e["type"] for e in second][-1] == "done"
    assert [c["marker"] for c in _citations(second)] == [3]
    assert _citations(second)[0]["title"] == "Exchange Policy"


async def test_a_turn_that_cites_nothing_emits_no_citations_but_still_completes(
    client: AsyncClient, org_headers: dict[str, str], fake_kb: object
) -> None:
    aid = await _rag_assistant(client, org_headers)
    cid = await _conversation(client, org_headers, aid)

    # No "search" in the prompt, so FakeDriver never calls kb_search.
    events = await _send(client, org_headers, cid, "just say hello")
    assert [e["type"] for e in events][-1] == "done"
    assert _citations(events) == []


async def test_citations_disabled_means_no_events_and_no_markers_in_the_tool_output(
    client: AsyncClient, org_headers: dict[str, str], fake_kb: object
) -> None:
    aid = await _rag_assistant(client, org_headers, citations=False)
    cid = await _conversation(client, org_headers, aid)

    events = await _send(client, org_headers, cid, "please search: refunds?")
    assert [e["type"] for e in events][-1] == "done"
    assert _citations(events) == []
    # And the tool output carries no [n] markers either — leaving them in
    # while nothing resolves them is worse than turning the feature off.
    (result,) = [e for e in events if e["type"] == "tool_result"]
    assert "[1]" not in result["output"]


async def test_a_budget_abort_still_resolves_citations(
    client: AsyncClient, org_headers: dict[str, str], fake_kb: object
) -> None:
    """Cost is only known at the driver's final UsageEvent, so tripping the
    budget is normally the last event of an otherwise complete turn. The
    answer gets persisted either way — it must not persist with live [n]
    markers and an empty sources panel."""
    aid = await _rag_assistant(client, org_headers, budget=0.0000001)
    cid = await _conversation(client, org_headers, aid)

    events = await _send(client, org_headers, cid, "please search: refunds?")
    kinds = [e["type"] for e in events]
    assert "error" in kinds
    assert kinds[-1] == "done", "the turn must still persist and finish"
    assert [c["marker"] for c in _citations(events)] == [1, 2]

    detail = (await client.get(f"/api/v1/conversations/{cid}", headers=org_headers)).json()
    msg = [m for m in detail["messages"] if m["role"] == "assistant"][-1]
    assert [b for b in msg["blocks"] if b.get("type") == "citation"]
