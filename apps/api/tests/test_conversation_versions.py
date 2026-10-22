"""Each run records the version that answered it.

A live conversation answers from the approved version it started on; a
preview from the draft (recorded as no version) or from the version it was
opened on (Phase 7b.1; `test_approval_gate.py` covers the gate itself).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.agent.events import TokenEvent, UsageEvent
from app.db.session import get_sessionmaker
from app.models.conversation import Run
from app.services import chat as chat_svc
from httpx import AsyncClient
from sqlalchemy import select
from tests.versions import PREVIEW, go_live

pytestmark = pytest.mark.anyio


class _PromptSpy:
    name = "spy"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def stream(self, *, spec: Any, **_: Any) -> Any:
        self.prompts.append(spec.system_prompt)
        yield TokenEvent(text="ok")
        yield UsageEvent(tokens_in=1, tokens_out=1)


async def _set_prompt(client: AsyncClient, h: dict[str, str], aid: str, prompt: str) -> None:
    cfg = (await client.get(f"/api/v1/assistants/{aid}", headers=h)).json()["draft_config"]
    cfg["system_prompt"] = prompt
    r = await client.put(f"/api/v1/assistants/{aid}/draft-config", json=cfg, headers=h)
    assert r.status_code == 200, r.text


async def _conversation(
    client: AsyncClient, h: dict[str, str], aid: str, body: dict[str, Any]
) -> uuid.UUID:
    r = await client.post(f"/api/v1/assistants/{aid}/conversations", json=body, headers=h)
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


async def _turn(cid: uuid.UUID) -> None:
    async with get_sessionmaker()() as session:
        async for _ in chat_svc.run_message(session, conversation_id=cid, text="hi"):
            pass


async def _run_versions(cid: uuid.UUID) -> list[int | None]:
    async with get_sessionmaker()() as s:
        rows = await s.scalars(
            select(Run).where(Run.conversation_id == cid).order_by(Run.created_at)
        )
        return [r.version_number for r in rows]


async def test_each_run_records_the_version_that_answered(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _PromptSpy()
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: spy)
    a = (await client.post("/api/v1/assistants", json={"name": "V"}, headers=org_headers)).json()
    aid = a["id"]
    await _set_prompt(client, org_headers, aid, "You are version one.")
    await go_live(client, aid, org_headers)
    await _set_prompt(client, org_headers, aid, "The draft.")

    live = await _conversation(client, org_headers, aid, {})
    draft = await _conversation(client, org_headers, aid, PREVIEW)
    pinned = await _conversation(client, org_headers, aid, {**PREVIEW, "version": 1})
    for cid in (live, draft, pinned):
        await _turn(cid)

    assert ["version one" in p for p in spy.prompts] == [True, False, True]
    assert "The draft." in spy.prompts[1]
    assert [await _run_versions(c) for c in (live, draft, pinned)] == [[1], [None], [1]]
