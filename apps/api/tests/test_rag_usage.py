"""Embedding and rerank usage reaches the ledger (task 1.8).

`usage_events.kind` had `embedding` and `rerank` from the start and nothing
wrote them. Every indexed document and every kb_search call was off the books.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from app.agent.events import TokenEvent, UsageEvent
from app.db.session import get_sessionmaker
from app.models.conversation import Conversation
from app.models.usage import UsageEvent as UsageRow
from app.models.usage import UsageKind
from app.rag import usage
from app.rag.embedders.voyage import VoyageEmbedder
from app.services import chat as chat_svc
from httpx import AsyncClient
from sqlalchemy import select
from tests.test_chat import _new_assistant, _new_conversation

pytestmark = pytest.mark.anyio


def test_voyage_usage_is_priced_and_local_usage_is_free() -> None:
    m = usage.Meter()
    m.add("embedding", "voyage-3-large", 1_000_000)
    m.add("rerank", "rerank-2.5", 2_000_000)
    m.add("embedding", "local-bge-m3", 5_000)
    by = {(line.kind, line.model): line for line in m.rows()}
    assert by[("embedding", "voyage-3-large")].cost_usd == pytest.approx(0.18)
    assert by[("rerank", "rerank-2.5")].cost_usd == pytest.approx(0.10)
    assert by[("embedding", "local-bge-m3")].cost_usd == 0
    assert by[("embedding", "local-bge-m3")].tokens == 5_000


def test_nothing_is_recorded_when_nothing_is_metering() -> None:
    usage.record("embedding", "voyage-3-large", 100)  # must simply not fail


async def test_the_voyage_client_reports_the_tokens_voyage_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No real call, no credits: the HTTP transport is mocked."""

    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.0] * 4}], "usage": {"total_tokens": 1234}},
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.rag.embedders.voyage.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(reply), **kw),
    )
    with usage.meter() as m:
        await VoyageEmbedder().embed_documents(["hello"])
    (line,) = m.rows()
    assert (line.kind, line.model, line.tokens) == ("embedding", "voyage-3-large", 1234)


class _RetrievingDriver:
    """Stands in for a turn whose kb_search embeds and reranks."""

    name = "retrieving"

    async def stream(self, **_: Any) -> Any:
        usage.record("embedding", "voyage-3-large", 1_000)
        usage.record("rerank", "rerank-2.5", 4_000)
        yield TokenEvent(text="answer")
        yield UsageEvent(tokens_in=10, tokens_out=5, cost_usd=0.001)


async def test_a_turns_retrieval_lands_on_the_ledger_and_the_bill(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.agent.runtime.get_driver", _RetrievingDriver)
    aid = await _new_assistant(client, org_headers)
    cid = uuid.UUID(await _new_conversation(client, org_headers, aid))
    async with get_sessionmaker()() as session:
        async for _ in chat_svc.run_message(session, conversation_id=cid, text="q"):
            pass

    async with get_sessionmaker()() as s:
        rows = (await s.scalars(select(UsageRow).where(UsageRow.conversation_id == cid))).all()
        conv = await s.get(Conversation, cid)
    by_kind = {r.kind: r for r in rows}
    assert set(by_kind) == {UsageKind.llm, UsageKind.embedding, UsageKind.rerank}
    assert by_kind[UsageKind.embedding].tokens_in == 1_000
    assert float(by_kind[UsageKind.rerank].cost_usd) == pytest.approx(0.0002)
    assert conv is not None
    assert float(conv.cost_usd) == pytest.approx(0.001 + 0.00018 + 0.0002)
