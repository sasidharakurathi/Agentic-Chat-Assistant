"""The CLI's end of the SDK's stdio protocol, driven by a script.

Shared by `test_driver_transport.py` (the driver against a scripted CLI)
and the eval regression gate (`tests/canned_model.py`), which plays a whole
suite through it. `driver.transport_factory` is the seam: the real
`ClaudeSDKDriver`, SDK client and message parser run, and nothing starts a
CLI or reaches a model.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport import Transport

SESSION = "sess-scripted"
Script = Callable[["ScriptedCLI"], Awaitable[None]]


class ScriptedCLI(Transport):
    """The CLI's end of the stdio protocol, driven by a script."""

    def __init__(self, options: ClaudeAgentOptions, script: Script) -> None:
        self.options = options
        self._script = script
        self._out: asyncio.Queue[dict[str, Any] | BaseException | None] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._task: asyncio.Task[None] | None = None
        self.initialize: dict[str, Any] = {}
        self.prompt: str | None = None
        self.interrupted = asyncio.Event()

    # ── Transport ────────────────────────────────────────────
    async def connect(self) -> None:
        return None

    def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def end_input(self) -> None:
        return None

    async def write(self, data: str) -> None:
        for line in data.splitlines():
            if line.strip():
                self._on_sdk_message(json.loads(line))

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._out.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    # ── what the SDK sent ────────────────────────────────────
    def _on_sdk_message(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "control_request":
            request = msg["request"]
            if request["subtype"] == "initialize":
                self.initialize = request
            elif request["subtype"] == "interrupt":
                self.interrupted.set()
            self.send(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": msg["request_id"],
                        "response": {},
                    },
                }
            )
        elif kind == "control_response":
            response = msg["response"]
            future = self._pending.pop(response["request_id"])
            if response["subtype"] == "success":
                future.set_result(response.get("response") or {})
            else:
                future.set_exception(RuntimeError(response.get("error")))
        elif kind == "user" and self.prompt is None:
            self.prompt = msg["message"]["content"]
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            await self._script(self)
        except Exception as exc:
            self._out.put_nowait(exc)

    # ── what the script sends ────────────────────────────────
    def send(self, msg: dict[str, Any]) -> None:
        self._out.put_nowait(msg)

    def end(self) -> None:
        self._out.put_nowait(None)

    def fail(self, exc: BaseException) -> None:
        self._out.put_nowait(exc)

    async def request(self, subtype: str, **fields: Any) -> dict[str, Any]:
        """A control request from the CLI to the SDK, answered by the SDK."""
        request_id = f"cli-{next(self._ids)}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self.send(
            {
                "type": "control_request",
                "request_id": request_id,
                "request": {"subtype": subtype, **fields},
            }
        )
        return await asyncio.wait_for(future, 5)

    async def mcp(
        self, server: str, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One JSON-RPC message to an in-process MCP server, as the CLI's MCP
        client sends it. Notifications (no id) get an empty ack."""
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not method.startswith("notifications/"):
            message["id"] = next(self._ids)
        reply = await self.request("mcp_message", server_name=server, message=message)
        response: dict[str, Any] = reply["mcp_response"]
        assert "error" not in response, response
        return response.get("result") or {}

    async def mcp_connect(self, server: str) -> list[dict[str, Any]]:
        """The handshake the CLI does before it can call a tool. Returns the
        server's tool list."""
        await self.mcp(
            server,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "scripted-cli", "version": "0"},
            },
        )
        await self.mcp(server, "notifications/initialized")
        tools: list[dict[str, Any]] = (await self.mcp(server, "tools/list"))["tools"]
        return tools

    def hook_ids(self, event: str) -> list[str]:
        matchers = (self.initialize.get("hooks") or {}).get(event) or []
        return [cid for m in matchers for cid in m["hookCallbackIds"]]


# ── CLI message builders (the shapes the bundled CLI emits) ────────


def delta(message_id: str, text: str) -> list[dict[str, Any]]:
    base = {"uuid": f"u-{message_id}", "session_id": SESSION, "parent_tool_use_id": None}
    return [
        {
            **base,
            "type": "stream_event",
            "event": {"type": "message_start", "message": {"id": message_id}},
        },
        {
            **base,
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        },
    ]


def assistant(message_id: str, blocks: list[dict[str, Any]], tin: int, tout: int) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "model": "claude-sonnet-5",
            "content": blocks,
            "usage": {"input_tokens": tin, "output_tokens": tout},
        },
        "parent_tool_use_id": None,
        "session_id": SESSION,
    }


def tool_result(tool_use_id: str, text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": text}],
                    "is_error": is_error,
                }
            ],
        },
        "parent_tool_use_id": None,
        "session_id": SESSION,
    }


def result(tin: int, tout: int, cost: float, **extra: Any) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "duration_ms": 12,
        "duration_api_ms": 10,
        "is_error": False,
        "num_turns": 2,
        "session_id": SESSION,
        "total_cost_usd": cost,
        "usage": {"input_tokens": tin, "output_tokens": tout},
        **extra,
    }


__all__ = ["SESSION", "Script", "ScriptedCLI", "assistant", "delta", "result", "tool_result"]
