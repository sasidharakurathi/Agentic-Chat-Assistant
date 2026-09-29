"""A conversation follows the assistant's current published version.

Conversations used to keep the version live when they started, so a publish
reached only new conversations. Now every turn reads the current version (or
the draft while nothing is published), and each run records which version
answered it.
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
from tests.test_chat import _new_conversation

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


async def _publish(client: AsyncClient, h: dict[str, str], aid: str) -> None:
    r = await client.post(f"/api/v1/assistants/{aid}/versions", json={"note": ""}, headers=h)
    assert r.status_code in (200, 201), r.text


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


async def test_an_existing_conversation_picks_up_a_new_version(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _PromptSpy()
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: spy)
    a = (await client.post("/api/v1/assistants", json={"name": "V"}, headers=org_headers)).json()
    aid = a["id"]

    await _set_prompt(client, org_headers, aid, "You are version one.")
    await _publish(client, org_headers, aid)
    cid = uuid.UUID(await _new_conversation(client, org_headers, aid))
    await _turn(cid)

    await _set_prompt(client, org_headers, aid, "You are version two.")
    await _turn(cid)  # draft changed, not published: still v1
    await _publish(client, org_headers, aid)
    await _turn(cid)  # same conversation, now v2

    assert "version one" in spy.prompts[0]
    assert "version one" in spy.prompts[1], "an unpublished draft must not leak in"
    assert "version two" in spy.prompts[2]
    assert await _run_versions(cid) == [1, 1, 2]


async def test_an_unpublished_assistant_runs_its_draft(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _PromptSpy()
    monkeypatch.setattr("app.agent.runtime.get_driver", lambda: spy)
    a = (await client.post("/api/v1/assistants", json={"name": "D"}, headers=org_headers)).json()
    await _set_prompt(client, org_headers, a["id"], "Draft prompt.")
    cid = uuid.UUID(await _new_conversation(client, org_headers, a["id"]))
    await _turn(cid)
    assert "Draft prompt." in spy.prompts[0]
    assert await _run_versions(cid) == [None]
