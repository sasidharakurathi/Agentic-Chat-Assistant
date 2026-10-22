"""HTTP actions (Phase 9.3): declared calls to a company system.

The SQL actions of 9.1, for systems with an API instead of a database. The
builder writes each call on the HTTP request tool's node, against one of
the assistant's HTTP connections:

    POST /tickets        params: title, details     tier: confirm

- The **connection** holds the base URL and the headers (an API key, a
  bearer token), sealed. The model never sees or sets either.
- `{name}` slots in the path are filled from the parameters, each value
  URL-quoted so it stays one path segment; `{asker_email}`, `{asker_name}`
  and `{asker_id}` come from the signed-in person.
- The other parameters are the query string of a GET or DELETE and the JSON
  body of anything else.
- When the connection says so (`send_asker`), the request carries who is
  asking (`X-Asker-Email`, `X-Asker-Name`, `X-Asker-Id`), to that host
  only, so the system can answer as them. A `confirm` action needs it: the
  person confirms their own request, so the system has to know who they are.
- Redirects are not followed: a redirect would carry the secret headers to
  wherever it points.

The tiers are those of SQL actions. A GET only reads, so it is `read`;
anything else changes something, so it is `confirm` or `approver`.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode, urlsplit

from app.agent.actions import (
    BOUND_SLOTS,
    ActionInputError,
    Asker,
    input_schema,
    qualified_name,
    tool_name,
    values_for,
)
from app.agent.caps import CapabilityTool, _err, _text
from app.logging import get_logger

if TYPE_CHECKING:
    from app.models.integration import HttpConnection
    from app.schemas.assistant_config import AssistantConfig, HttpAction

log = get_logger(__name__)

#: A path slot: `{ticket_id}`.
SLOT = re.compile(r"\{([a-z][a-z0-9_]*)\}")
#: A response field to keep: `id`, `data.status`.
FIELD = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")
#: Methods whose other parameters are the query string, not a body.
QUERY_METHODS = frozenset({"GET", "DELETE"})
#: What the model reads of an answer.
BODY_CHARS = 20_000
HTTP_ERROR_FROM = 400

_PATH_CHARS = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/{}-]*$")


@dataclass(frozen=True)
class HttpCall:
    """One rendered call: what is sealed, shown to the decider, and sent."""

    method: str
    #: The path and query string, under the connection's base URL.
    path: str
    body: dict[str, Any] | None

    def payload(self) -> dict[str, Any]:
        return {"method": self.method, "path": self.path, "body": self.body}

    @classmethod
    def of(cls, raw: Any) -> HttpCall:
        if not isinstance(raw, dict):
            raise ValueError("the request is missing")
        body = raw.get("body")
        return cls(str(raw.get("method", "")), str(raw.get("path", "")), body or None)

    def statement(self, base_url: str) -> str:
        """The call in words, for the decider and the record."""
        line = f"{self.method} {base_url}{self.path}"
        if self.body is None:
            return line
        return line + "\n" + json.dumps(self.body, indent=2, ensure_ascii=False, sort_keys=True)


# ── checking (when the config is saved) ──────────────────────


def check_http_action(action: HttpAction) -> None:
    """Refuse a call that can't be made safely, with a reason the builder can
    act on. Raises ValueError (a validation error on save)."""
    where = f"action {action.name!r}"
    path = action.path
    if not path.startswith("/"):
        raise ValueError(f"{where}: the path starts with /, like /tickets")
    if "://" in path or "//" in path or ".." in path or "\\" in path:
        raise ValueError(
            f"{where}: the path stays under the connection's address: no //, .. or ://"
        )
    if "?" in path or "#" in path:
        raise ValueError(
            f"{where}: the path has no query: values not in the path become the query or the body"
        )
    if not _PATH_CHARS.match(path):
        raise ValueError(f"{where}: the path has characters a URL path can't")
    if "{" in SLOT.sub("", path) or "}" in SLOT.sub("", path):
        raise ValueError(f"{where}: write a slot as {{name}}, lowercase")
    used = SLOT.findall(path)
    declared = {p.name: p for p in action.params}
    if unknown := sorted(set(used) - set(declared) - BOUND_SLOTS):
        raise ValueError(f"{where}: no parameter named {', '.join(unknown)}")
    if optional := sorted(n for n in used if n in declared and not declared[n].required):
        raise ValueError(
            f"{where}: a path slot is always filled in, so make {optional[0]} required"
        )
    for name in action.fields:
        if not FIELD.match(name):
            raise ValueError(f"{where}: {name!r} isn't a field like id or data.status")
    if action.method == "GET" and action.tier != "read":
        raise ValueError(f"{where}: a GET only reads, so its tier is 'read'")
    if action.method != "GET" and action.tier == "read":
        raise ValueError(f"{where}: {action.method} changes something: 'confirm' or 'approver'")


# ── finding and rendering ────────────────────────────────────


def find_http(config: AssistantConfig, qualified: str) -> HttpAction | None:
    """The HTTP action a qualified tool name stands for, if the tool is on."""
    tool = config.tools.http_request
    if not tool.enabled:
        return None
    return next((a for a in tool.actions if qualified_name(a) == qualified), None)


def _query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render(
    action: HttpAction, args: dict[str, Any], *, asker: Asker
) -> tuple[HttpCall, dict[str, Any]]:
    """The call that would be made, and the checked parameters."""
    values = values_for(action, args)
    slots = {**values, **asker.slots()}
    used = set(SLOT.findall(action.path))

    def fill(match: re.Match[str]) -> str:
        value = slots.get(match.group(1))
        if value is None or value == "":
            raise ActionInputError(f"{match.group(1)} is required")
        segment = quote(_query_value(value), safe="")
        if segment.strip(".") == "":
            # `.` and `..` survive quoting, and a URL resolves them: the
            # call would climb out of the path the builder wrote.
            raise ActionInputError(f"{match.group(1)} can't be {value!r}")
        return segment

    path = SLOT.sub(fill, action.path)
    rest = {k: v for k, v in values.items() if k not in used and v is not None}
    body: dict[str, Any] | None = None
    if action.method in QUERY_METHODS:
        if rest:
            path += "?" + urlencode({k: _query_value(v) for k, v in sorted(rest.items())})
    else:
        body = rest
    return HttpCall(action.method, path, body), values


# ── sending ──────────────────────────────────────────────────


def asker_headers(asker: Asker) -> dict[str, str]:
    """Who is asking, as headers. A name that isn't plain ASCII is
    percent-encoded: header values are bytes, not text."""
    name = asker.name if asker.name.isascii() and asker.name.isprintable() else quote(asker.name)
    return {
        "X-Asker-Email": asker.email,
        "X-Asker-Name": name,
        "X-Asker-Id": str(asker.user_id),
    }


def _kept(data: Any, fields: list[str]) -> Any:
    """Only the named fields of a JSON answer (each item's, for a list)."""
    if isinstance(data, list):
        return [_kept(item, fields) for item in data]
    if not isinstance(data, dict):
        return data
    out: dict[str, Any] = {}
    for name in fields:
        node: Any = data
        for part in name.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node is not None:
            out[name] = node
    return out


def answer(status: int, reason: str, content_type: str, body: bytes, fields: list[str]) -> str:
    """The answer as the model reads it: the status, then the body, trimmed
    to the action's fields when it names some."""
    head = f"HTTP {status} {reason}".rstrip()
    text = body.decode("utf-8", errors="replace")
    if content_type.endswith("json"):
        try:
            data = json.loads(text)
        except ValueError:
            pass
        else:
            text = json.dumps(_kept(data, fields) if fields else data, indent=2, ensure_ascii=False)
    elif fields:
        text = "(the answer isn't JSON, so its fields can't be picked)"
    if len(text) > BODY_CHARS:
        text = text[:BODY_CHARS] + f"\n[answer cut at {BODY_CHARS:,} characters]"
    return f"{head}\n\n{text}" if text else head


async def connection(assistant_id: uuid.UUID, connection_id: str) -> HttpConnection | None:
    """The assistant's own connection with that id, or None."""
    from app.db.session import get_sessionmaker
    from app.services import http_connections

    try:
        cid = uuid.UUID(connection_id)
    except ValueError:
        return None
    async with get_sessionmaker()() as session:
        try:
            return await http_connections.get(session, assistant_id=assistant_id, connection_id=cid)
        except Exception:
            return None


async def send(
    assistant_id: uuid.UUID, action: HttpAction, call: HttpCall, asker: Asker | None
) -> dict[str, Any]:
    """Make the call with the connection's headers, as a tool result."""
    from app.db.session import get_sessionmaker
    from app.security.ssrf import FetchLimits, SsrfBlocked, safe_request
    from app.services import destinations, http_connections

    conn = await connection(assistant_id, action.connection_id)
    if conn is None:
        return _err("error: this action's connection is not set up")
    if conn.send_asker and asker is None:
        return _err("error: this action needs a signed-in person")
    async with get_sessionmaker()() as session:
        secret = await http_connections.open_headers(session, conn)
    headers = {**secret, "Accept": "application/json"}
    if call.body is not None:
        headers["Content-Type"] = "application/json"
    if conn.send_asker and asker is not None:
        headers.update(asker_headers(asker))
    host = urlsplit(conn.base_url).hostname or ""
    try:
        result = await safe_request(
            call.method,
            conn.base_url + call.path,
            headers=headers,
            body=json.dumps(call.body) if call.body is not None else None,
            allowed_domains=[host],
            # The secret headers go to this address and nowhere else.
            limits=FetchLimits(max_redirects=0),
            internal=await destinations.for_org(conn.org_id),
        )
    except SsrfBlocked as exc:
        log.info("http_action_blocked", action=action.name, reason=str(exc))
        return _err(f"blocked: {exc}")
    except Exception as exc:
        return _err(f"error: {exc}")
    out = answer(result.status, result.reason, result.content_type, result.body, action.fields)
    return _err(out) if result.status >= HTTP_ERROR_FROM else _text(out)


def build_http_action_tools(
    assistant_id: uuid.UUID, actions: list[HttpAction], asker: Asker | None
) -> list[CapabilityTool]:
    """One tool per HTTP action. As with SQL actions, only a `read` action's
    handler runs in a turn; the router stores the others as requests."""
    return [
        CapabilityTool(
            name=tool_name(action),
            description=_description(action),
            input_schema=input_schema(action),
            handler=_reader(assistant_id, action, asker),
            read_only=action.tier == "read",
            open_world=True,
        )
        for action in actions
    ]


def _description(action: HttpAction) -> str:
    text = action.title + (f". {action.description}" if action.description else "")
    if action.tier == "confirm":
        text += " The person asking confirms it on a card before it is sent."
    elif action.tier == "approver":
        text += " It waits for an approver before it is sent."
    return text


def _reader(assistant_id: uuid.UUID, action: HttpAction, asker: Asker | None) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        if action.tier != "read":
            return _err("error: this action runs only after it is decided")
        if asker is None:
            return _err("error: this action needs a signed-in person")
        try:
            call, _values = render(action, args, asker=asker)
        except ActionInputError as exc:
            return _err(f"error: {exc}")
        return await send(assistant_id, action, call, asker)

    return handler


__all__ = [
    "HttpCall",
    "asker_headers",
    "build_http_action_tools",
    "check_http_action",
    "connection",
    "find_http",
    "render",
    "send",
]
