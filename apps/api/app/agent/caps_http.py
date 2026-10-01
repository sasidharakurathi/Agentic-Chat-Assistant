"""The `http_request` capability (task 4.1, plan §4.3).

The model can call a web API or read a page: `method`, `url`, optional
`headers` and `body`. Every request goes through `app.security.ssrf`, so it
can only reach public addresses (and, when the builder set one, the
assistant's domain allowlist), and each redirect is checked again.

What the model gets back is text: the status line, the content type, the
final URL if it was redirected, and the body when it is text-like, capped. A
binary body is described, not dumped.

Approval is decided before this handler runs (`approvals.classify`): GET and
HEAD read, anything else changes something somewhere and asks a human unless
the assistant says otherwise.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from app.agent.caps import CapabilityTool
from app.logging import get_logger
from app.schemas.assistant_config import HttpRequestTool
from app.security.ssrf import (
    DEFAULT_LIMITS,
    FetchLimits,
    FetchResult,
    SsrfBlocked,
    is_compressed,
    safe_request,
)

log = get_logger(__name__)

#: Methods that only read. Everything else is treated as a change.
READ_METHODS = frozenset({"GET", "HEAD"})
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")

#: How much of a text body the model sees. The post-tool cap is the backstop;
#: this keeps one page from filling most of the context on its own.
BODY_CHARS = 20_000
MAX_REQUEST_BODY = 100_000
#: Statuses from 400 up are failures: the call is marked as an error.
HTTP_ERROR_FROM = 400

_TEXT_TYPES = ("text/", "application/json", "application/xml", "application/javascript")
_TEXT_SUFFIXES = ("+json", "+xml")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "method": {
            "type": "string",
            "enum": list(METHODS),
            "description": "HTTP method. Defaults to GET.",
        },
        "url": {"type": "string", "description": "Absolute http(s) URL."},
        "headers": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Optional request headers.",
        },
        "body": {"type": "string", "description": "Optional request body (e.g. JSON text)."},
    },
    "required": ["url"],
}


def _is_text(content_type: str) -> bool:
    return content_type.startswith(_TEXT_TYPES) or content_type.endswith(_TEXT_SUFFIXES)


def render(result: FetchResult) -> str:
    """The result as the model reads it."""
    lines = [f"HTTP {result.status} {result.reason}".rstrip()]
    ctype = result.content_type or "unknown content type"
    size = f"{len(result.body):,} bytes" + (" (truncated)" if result.truncated else "")
    lines.append(f"{ctype} · {size}")
    if result.redirects:
        lines.append(f"final URL after {len(result.redirects)} redirect(s): {result.url}")
    if is_compressed(result.headers):
        # Asked for uncompressed; never inflated here (see `security/ssrf.py`).
        lines.append("(the body was sent compressed and was not read)")
        return "\n".join(lines)
    if not result.body:
        return "\n".join(lines)
    if not _is_text(result.content_type) and result.content_type:
        lines.append("(binary body not shown)")
        return "\n".join(lines)

    text = result.body.decode("utf-8", errors="replace")
    if result.content_type == "application/json" and not result.truncated:
        with contextlib.suppress(ValueError):
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    if len(text) > BODY_CHARS:
        text = text[:BODY_CHARS] + f"\n[body cut at {BODY_CHARS:,} characters]"
    return "\n".join(lines) + "\n\n" + text


def build_http_tool(
    cfg: HttpRequestTool, *, limits: FetchLimits = DEFAULT_LIMITS
) -> CapabilityTool:
    """The tool for one assistant: it closes over that assistant's allowlist,
    so a model cannot widen it by asking."""
    allowed = list(cfg.allowed_domains)

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        method = str(args.get("method") or "GET").upper()
        if method not in METHODS:
            return _err(f"error: unsupported method {method}")
        url = str(args.get("url") or "").strip()
        if not url:
            return _err("error: url is required")
        headers = args.get("headers") or {}
        if not isinstance(headers, dict):
            return _err("error: headers must be an object of strings")
        body = args.get("body")
        if body is not None and not isinstance(body, str):
            body = json.dumps(body)
        if body is not None and len(body) > MAX_REQUEST_BODY:
            return _err(f"error: request body is larger than {MAX_REQUEST_BODY:,} characters")
        try:
            result = await safe_request(
                method,
                url,
                headers={str(k): str(v) for k, v in headers.items()},
                body=body,
                allowed_domains=allowed,
                limits=limits,
            )
        except SsrfBlocked as exc:
            log.info("http_request_blocked", reason=str(exc))
            return _err(f"blocked: {exc}")
        out = render(result)
        return _err(out) if result.status >= HTTP_ERROR_FROM else _text(out)

    description = (
        "Make an HTTP request to a public web API or page and return the status and body. "
        "GET and HEAD run directly; other methods change something and may need a person "
        "to approve them."
    )
    if allowed:
        description += (
            f" Only these domains (and their subdomains) are reachable: {', '.join(allowed)}."
        )
    return CapabilityTool(
        name="http_request",
        description=description,
        input_schema=_SCHEMA,
        handler=handler,
        read_only=False,
        open_world=True,
    )


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _err(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}], "is_error": True}


__all__ = ["BODY_CHARS", "METHODS", "READ_METHODS", "build_http_tool", "render"]
