"""In-process capability tools exposed to the agent as an SDK MCP server.

Phase 1 ships two safe, read-only tools: ``calculator`` and ``datetime``. RAG,
SQL, HTTP and MongoDB tools land in phases 2-4 and register here too.

Each tool is a plain async ``handler(args) -> {"content": [...]}``. ``build_caps_server``
adapts them to ``claude_agent_sdk``. Keeping the handlers SDK-agnostic means they
are unit-testable without the CLI.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig
from mcp.types import ToolAnnotations

from app.agent.post_tool import run_capability

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

CAPS_SERVER_NAME = "caps"


@dataclass(frozen=True)
class CapabilityTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    read_only: bool = True
    #: Reaches beyond systems this deployment controls (the public web).
    open_world: bool = False
    #: The in-process SDK server it belongs to: `caps` for the platform's
    #: own tools, a registered MCP server's name for its tools (task 4.6).
    server: str = CAPS_SERVER_NAME

    @property
    def qualified_name(self) -> str:
        return f"mcp__{self.server}__{self.name}"


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _err(s: str) -> dict[str, Any]:
    """A refusal or failure, not an answer.

    The `is_error` flag is what turns a tool result red in the UI and tells
    the model it did not get what it asked for. Without it a guard's
    "blocked: ..." string is delivered as a perfectly successful call, which
    reads to a user as though the query ran.
    """
    return {"content": [{"type": "text", "text": s}], "is_error": True}


# ── calculator ───────────────────────────────────────────────

_BIN_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_MAX_POW_EXPONENT = 1000


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_safe_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW_EXPONENT:
            raise ValueError("exponent too large")
        return _BIN_OPS[type(node.op)](left, right)
    raise ValueError("unsupported expression")


async def _calculator(args: dict[str, Any]) -> dict[str, Any]:
    expr = str(args.get("expression", "")).strip()
    if not expr:
        return _err("error: empty expression")
    try:
        result = _safe_eval(ast.parse(expr, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
        return _err(f"error: {exc}")
    return _text(f"{expr} = {result}")


CALCULATOR = CapabilityTool(
    name="calculator",
    description="Evaluate a basic arithmetic expression (+, -, *, /, //, %, **, parentheses).",
    input_schema={"expression": str},
    handler=_calculator,
)


# ── datetime ─────────────────────────────────────────────────


async def _datetime(args: dict[str, Any]) -> dict[str, Any]:
    tz_name = args.get("tz")
    now_utc = datetime.now(UTC)
    if not tz_name:
        return _text(f"UTC now: {now_utc.isoformat()}")
    try:
        tz = ZoneInfo(str(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        return _err(f"error: unknown timezone {tz_name!r}")
    return _text(f"{tz_name}: {now_utc.astimezone(tz).isoformat()}")


DATETIME = CapabilityTool(
    name="datetime",
    description="Return the current date and time (UTC, or in an IANA timezone via `tz`).",
    input_schema={"tz": str},
    handler=_datetime,
)


ALL_CAPS: dict[str, CapabilityTool] = {t.name: t for t in (CALCULATOR, DATETIME)}


def annotations_for(t: CapabilityTool) -> ToolAnnotations:
    """MCP tool annotations (plan §4.3): what a tool may do, stated to the
    model and the SDK instead of left for them to guess from the name.

    `read_only` was already declared on every capability and then dropped on
    the way into the SDK. A tool that can write (`sql_query`) is flagged
    destructive; nothing here is idempotent unless it is read-only.
    """
    return ToolAnnotations(
        read_only_hint=t.read_only,
        destructive_hint=not t.read_only,
        idempotent_hint=t.read_only,
        open_world_hint=t.open_world,
    )


def build_sdk_server(name: str, tools: list[CapabilityTool]) -> McpSdkServerConfig:
    """Wrap capability tools as a claude_agent_sdk in-process MCP server."""
    sdk_tools = [
        tool(t.name, t.description, t.input_schema, annotations=annotations_for(t))(
            _wrap(t.handler)
        )
        for t in tools
    ]
    return create_sdk_mcp_server(name=name, version="0.1.0", tools=sdk_tools)


def build_caps_server(tools: list[CapabilityTool]) -> McpSdkServerConfig:
    return build_sdk_server(CAPS_SERVER_NAME, tools)


def build_sdk_servers(tools: list[CapabilityTool]) -> dict[str, McpSdkServerConfig]:
    """One in-process server per `server` name: `caps`, plus one for each
    registered MCP server this turn uses."""
    groups: dict[str, list[CapabilityTool]] = {}
    for t in tools:
        groups.setdefault(t.server, []).append(t)
    return {name: build_sdk_server(name, group) for name, group in groups.items()}


def _wrap(handler: ToolHandler) -> ToolHandler:
    async def _inner(args: dict[str, Any]) -> dict[str, Any]:
        # Every capability result is size-capped and secret-stripped on its
        # way to the model (app/agent/post_tool.py).
        return await run_capability(handler, args)

    return _inner


__all__ = [
    "ALL_CAPS",
    "CALCULATOR",
    "CAPS_SERVER_NAME",
    "DATETIME",
    "CapabilityTool",
    "annotations_for",
    "build_caps_server",
    "build_sdk_server",
    "build_sdk_servers",
]
