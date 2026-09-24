"""The post-tool step, tool-call records and tool usage (task 1.10).

- Every capability result is size-capped and has credential-shaped values
  replaced before the model sees it (both the SDK path and the FakeDriver
  path go through `run_capability`).
- Each tool call is recorded in `tool_calls` with its server, status and
  latency. It existed only as UI-shaped JSON on the message.
- Web searches the API ran are counted on the usage ledger.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from app.agent.driver import _UsageLedger
from app.agent.events import TokenEvent, UsageEvent
from app.agent.post_tool import MODEL_OUTPUT_CHARS, finish, run_capability, strip_secrets
from app.db.session import get_sessionmaker
from app.models.conversation import ToolCall
from app.models.usage import UsageEvent as UsageRow
from app.models.usage import UsageKind
from app.services import chat as chat_svc
from httpx import AsyncClient
from sqlalchemy import select
from tests.test_chat import _new_assistant, _new_conversation, _send

pytestmark = pytest.mark.anyio

SECRETS = {
    "anthropic": "sk-ant-api03-" + "A" * 40,
    "openai": "sk-proj-" + "b" * 40,
    "aws": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github": "ghp_" + "c" * 36,
    "slack": "xoxb-1234567890-abcdefghij",
    # jwt.io's public sample token, not a real credential.
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "pem": "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n-----END RSA PRIVATE KEY-----",
}


@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_credential_shapes_are_replaced(kind: str) -> None:
    out = strip_secrets(f"row 1: {SECRETS[kind]} end")
    assert SECRETS[kind] not in out and "<redacted>" in out


def test_bearer_tokens_and_uri_passwords_keep_their_shape() -> None:
    out = strip_secrets(
        "Authorization: Bearer abcdef0123456789abcdef and postgres://app:s3cr3t@db/x"
    )
    assert out == "Authorization: Bearer <redacted> and postgres://app:<redacted>@db/x"


def test_ordinary_values_are_left_alone() -> None:
    """Deliberately narrow: ids, hashes and prose must survive."""
    text = (
        f"id {uuid.uuid4()} sha256 {'e' * 64} commit 3f2a9c1 order sk-12 "
        "the skeleton key and the bearer of news"
    )
    assert strip_secrets(text) == text


def test_an_oversized_result_is_capped_and_says_so() -> None:
    result = finish({"content": [{"type": "text", "text": "x" * (MODEL_OUTPUT_CHARS + 500)}]})
    text = result["content"][0]["text"]
    assert text.startswith("x" * 100)
    assert "[truncated: 500 more characters" in text


async def test_run_capability_applies_both_steps() -> None:
    async def leaky(_args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": f"key={SECRETS['anthropic']}"}]}

    out = await run_capability(leaky, {})
    assert out["content"][0]["text"] == "key=<redacted>"


async def test_tool_calls_are_recorded_with_latency(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    aid = await _new_assistant(client, org_headers, {"tools": {"calculator": {"enabled": True}}})
    cid = await _new_conversation(client, org_headers, aid)
    await _send(client, org_headers, cid, "please calculate 21 + 21")

    async with get_sessionmaker()() as s:
        (row,) = (
            await s.scalars(select(ToolCall).where(ToolCall.conversation_id == uuid.UUID(cid)))
        ).all()
    assert (row.tool_name, row.server, row.status) == ("mcp__caps__calculator", "caps", "success")
    assert row.latency_ms is not None and row.latency_ms >= 0
    assert "42" in (row.output or "")


def test_the_ledger_reads_web_searches_from_the_final_usage() -> None:
    result = SimpleNamespace(
        usage={
            "input_tokens": 1,
            "output_tokens": 1,
            "server_tool_use": {"web_search_requests": 3},
        },
        total_cost_usd=0.01,
        session_id="s",
        num_turns=1,
        terminal_reason=None,
    )
    assert _UsageLedger().settle(result).web_searches == 3


class _Searching:
    name = "searching"

    async def stream(self, **_: Any) -> Any:
        yield TokenEvent(text="found it")
        yield UsageEvent(tokens_in=5, tokens_out=5, cost_usd=0.001, web_searches=2)


async def test_web_searches_land_on_the_ledger(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.agent.runtime.get_driver", _Searching)
    aid = await _new_assistant(client, org_headers)
    cid = uuid.UUID(await _new_conversation(client, org_headers, aid))
    async with get_sessionmaker()() as session:
        async for _ in chat_svc.run_message(session, conversation_id=cid, text="q"):
            pass
    async with get_sessionmaker()() as s:
        (row,) = (
            await s.scalars(
                select(UsageRow).where(
                    UsageRow.conversation_id == cid, UsageRow.kind == UsageKind.tool
                )
            )
        ).all()
    assert (row.model, row.tokens_in) == ("web_search", 2)


async def test_a_turn_never_streams_a_secret_a_tool_returned(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the driver, not just the helper: a row holding an
    API key is redacted before the model (and the client) see it."""
    from app.agent.caps import CapabilityTool
    from tests.test_chat_approvals import _assistant_with_db, _conversation

    async def leaky(_args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": f"api_key | {SECRETS['anthropic']}"}]}

    def build(_aid: uuid.UUID, _dbs: list[Any]) -> list[CapabilityTool]:
        return [CapabilityTool("sql_query", "stub", {"type": "object"}, leaky)]

    monkeypatch.setattr("app.agent.options.build_sql_tools", build)
    monkeypatch.setattr("app.agent.options.build_mongo_tools", lambda *_a, **_k: [])
    aid = await _assistant_with_db(client, org_headers)
    cid = uuid.UUID(await _conversation(client, org_headers, aid))
    async with get_sessionmaker()() as session:
        events = [
            e
            async for e in chat_svc.run_message(
                session, conversation_id=cid, text="sql: SELECT * FROM api_keys"
            )
        ]
    (result,) = [e for e in events if e.type == "tool_result"]
    assert SECRETS["anthropic"] not in result.output and "<redacted>" in result.output
