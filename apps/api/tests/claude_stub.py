"""The official SDK's own client, on a mock transport, for tests.

`install()` points `claude_api.client_factory` at a real `AsyncAnthropic`
whose HTTP client is an `httpx2.MockTransport`: the SDK builds the requests,
types the errors and makes its retries exactly as in production, and
nothing leaves the process. Its retry waits are recorded, not slept.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import anthropic._base_client as sdk_base
import httpx2
import pytest
from anthropic import AsyncAnthropic
from app.agent import claude_api

Handler = Callable[[httpx2.Request], httpx2.Response]


def message(
    text: str,
    *,
    tokens_in: int = 500,
    tokens_out: int = 20,
    stop_reason: str = "end_turn",
    model: str = "claude-haiku-4-5",
) -> httpx2.Response:
    """A Messages API answer with one text block."""
    return httpx2.Response(
        200,
        json={
            "id": "msg_stub",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
        },
    )


def error(
    status: int, kind: str = "api_error", headers: dict[str, str] | None = None
) -> httpx2.Response:
    return httpx2.Response(
        status,
        headers=headers,
        json={"type": "error", "error": {"type": kind, "message": "stub"}},
    )


def body(request: httpx2.Request) -> dict[str, Any]:
    return dict(json.loads(request.content))


def prompt(request: httpx2.Request) -> str:
    """The (single) user message a request carried."""
    return str(body(request)["messages"][0]["content"])


@dataclass
class Stub:
    requests: list[httpx2.Request] = field(default_factory=list)
    #: The waits the SDK asked for between retries, in seconds.
    slept: list[float] = field(default_factory=list)
    #: The timeout each client was built with.
    timeouts: list[float] = field(default_factory=list)


def install(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> Stub:
    stub = Stub()

    def record(request: httpx2.Request) -> httpx2.Response:
        stub.requests.append(request)
        return handler(request)

    def factory(timeout_s: float) -> AsyncAnthropic:
        stub.timeouts.append(timeout_s)
        return AsyncAnthropic(
            api_key="sk-ant-stub",
            timeout=timeout_s,
            max_retries=claude_api.MAX_RETRIES,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(record)),
        )

    async def no_sleep(seconds: float) -> None:
        stub.slept.append(seconds)

    monkeypatch.setattr(claude_api, "client_factory", factory)
    # The SDK waits between retries with `anyio.sleep` (its only use there).
    monkeypatch.setattr(sdk_base, "anyio", SimpleNamespace(sleep=no_sleep))
    return stub


__all__ = ["Stub", "body", "error", "install", "message", "prompt"]
