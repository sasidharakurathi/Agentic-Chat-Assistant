"""Tool-permission policy and the human-in-the-loop router (`can_use_tool`).

Deny-by-default (ADR 0003): anything not explicitly allowed is refused.
Phase 1 shipped the classifier with the "wait for a human" half stubbed out;
task 3.8 filled it in with a wait inside the turn. Phase 9.1 (plan D17)
takes the wait out: a call that needs a person is **stored as a request**,
with its exact input sealed, and the turn goes on and ends. The asker (for
an action on their own rows) or an approver decides whenever they can, and
the executor (`services/executor.py`) runs it once, afterwards.

**Risk is derived by parsing, not by trusting the tool name.** `sql_query` is
not inherently dangerous: `SELECT count(*)` and `DROP TABLE users` arrive
through the same tool. Classifying on the name alone would either prompt for
every read (users click "approve" reflexively, which is worse than no
prompt) or wave through every write. So the statement goes through
`sql_guard` here, and only a genuinely mutating one asks for a human.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from app.agent.caps import CAPS_SERVER_NAME
from app.agent.caps_http import READ_METHODS
from app.datasources.sql_guard import GuardResult, SqlBlocked, guard
from app.datasources.sql_guard import Permissions as SqlPermissions
from app.logging import get_logger
from app.schemas.assistant_config import (
    ApprovalMode,
    ApprovalPolicy,
    DatabaseRef,
    HttpRequestTool,
    McpServerRef,
)
from app.security.redact import is_secret_key, strip_secrets

log = get_logger(__name__)

Risk = Literal["low", "medium", "high"]
#: How a call is handled: `auto` runs, `deny` refuses, `require` stores a
#: request for an approver, `confirm` one for the asker (a declared action
#: on their own rows, Phase 9.1).
Mode = Literal["auto", "require", "deny", "confirm"]
#: Who decides a stored request.
Tier = Literal["asker", "approver"]

CanUseTool = Callable[
    [str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResultAllow | PermissionResultDeny],
]


def _qual(name: str) -> str:
    return f"mcp__{CAPS_SERVER_NAME}__{name}"


_READ_ONLY_CAPS = {
    _qual("calculator"),
    _qual("datetime"),
    _qual("kb_search"),
    _qual("kb_list_sources"),
    _qual("sql_list_schemas"),
    _qual("sql_introspect"),
    _qual("mongo_find"),
    _qual("mongo_aggregate"),
}

#: Tools that write, but only to the conversation owner's own notes for this
#: assistant (the memory tool, task 5.2): nobody else's data and nothing
#: outside the platform. Asking a person before every note would make the
#: tool unusable.
_OWN_NOTES = {_qual("memory")}

#: Getting a person for the person asking (Phase 9.5, `caps_handoff`): it
#: tells one contact inside the organisation where to look. Asking someone
#: first would defeat it: the person wants help now.
_OWN_REQUESTS = {_qual("hand_off")}

#: SDK built-ins that take no action of their own.
_NO_ACTION_BUILTINS = {
    "WebSearch",
    # Delegating to a subagent is not itself an action: every tool the
    # subagent then calls comes back through this router on its own. It only
    # exists when subagents are configured (`options.builtin_tools` and the
    # PreToolUse gate enforce that).
    "Agent",
}

#: How much of an HTTP request body the approval card shows.
_BODY_PREVIEW = 2_000


@dataclass(frozen=True)
class RequestOutcome:
    """What became of a call that needs a person (Phase 9.1).

    `queued`: stored as a request; `message` tells the model what happens
    next. `refused`: it could not be stored (the parameters don't fit, the
    connection can't write); `message` says why, so the model can fix it.
    `unattended`: an eval, with nobody to ask."""

    decision: Literal["queued", "refused", "unattended"]
    message: str = ""


class Requester(Protocol):
    """async (tool_name, input, risk, rationale, *, tier, tool_use_id) -> outcome"""

    def __call__(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        risk: Risk,
        rationale: str,
        *,
        tier: Tier,
        tool_use_id: str | None = None,
    ) -> Awaitable[RequestOutcome]: ...


@dataclass
class ApprovalRequest:
    """What the router needs from the turn to store a request. Supplied by
    `chat.run_message`; absent on paths with no conversation (a dry run), in
    which case anything needing a human is simply denied."""

    conversation_id: uuid.UUID
    org_id: uuid.UUID
    request: Requester


def sanitize(tool_input: dict[str, Any]) -> dict[str, Any]:
    """The input that will run, pinned at the moment a person is asked.

    The dict the SDK hands the permission callback is a live object. A deep
    copy taken at once is what gets sealed (Phase 9.1), so the statement
    that executes is the one the person read, whatever touches the original
    afterwards (plan §4.4 `updated_input=sanitize(input)`)."""
    return copy.deepcopy(tool_input)


def redact(value: Any) -> Any:
    """Strip credentials before an input is shown, stored or logged.

    By key (`password`, `authorization`, …) and, since task 4.7, by shape: a
    token in an argument with an ordinary name (`query`, `body`) used to be
    stored as-is in the audit trail. The model already had the original;
    this is only what people and the database see.
    """
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if is_secret_key(str(k)) and v is not None else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return strip_secrets(value)
    return value


def describe(tool_name: str, tool_input: dict[str, Any]) -> str:
    """The one line a reviewer actually reads.

    For SQL this is the **exact statement** — the plan is explicit that an
    approval card shows what will run, not a summary of it. "Approve a
    database write?" is unanswerable; the statement is answerable.
    """
    # The prefix only: a server's own tool could be *named* `mcp__caps__sql_query`,
    # and replacing that anywhere showed the reviewer a platform tool's name.
    short = tool_name.removeprefix(f"mcp__{CAPS_SERVER_NAME}__")
    if short == "sql_query":
        return str(tool_input.get("sql", "")).strip()
    if short == "http_request":
        # The request as a person would read it: the line that says what and
        # where, then what would be sent. Header values that look like
        # credentials are redacted, the rest shown.
        method = str(tool_input.get("method") or "GET").upper()
        lines = [f"{method} {strip_secrets(str(tool_input.get('url', '')))}"]
        headers = tool_input.get("headers")
        if isinstance(headers, dict):
            lines += [f"{k}: {v}" for k, v in redact(headers).items()]
        body = tool_input.get("body")
        if body:
            # Credential-shaped parts are masked (a `password` in a JSON
            # body, a key in a query string); the rest is what will be sent.
            text = strip_secrets(body) if isinstance(body, str) else str(redact(body))
            lines += ["", text if len(text) <= _BODY_PREVIEW else text[:_BODY_PREVIEW] + " …"]
        return "\n".join(lines)
    if short.startswith("mcp__"):
        # A registered MCP server's tool: say which server, in plain words.
        _, server, tool = [*short.split("__", 2), "", ""][:3]
        return f"{server}: {tool}({redact(tool_input)})"
    return f"{short}({redact(tool_input)})"


_STRICTNESS: dict[ApprovalMode, int] = {"auto": 0, "require": 1, "deny": 2}


def stricter(a: ApprovalMode, b: ApprovalMode) -> ApprovalMode:
    return a if _STRICTNESS[a] >= _STRICTNESS[b] else b


def mcp_mode(policy: ApprovalPolicy, ref: McpServerRef, tool: str) -> ApprovalMode:
    """The rule for one MCP tool (task 4.7): the most specific one set.

    The tool's own rule, else the server's, else the assistant's
    `mcp_default`. One exception: `mcp_default: deny` switches every MCP
    tool off, whatever the finer rules say, so there is always one place to
    stop them all.

    `auto` here still only skips the human for a tool the server declares
    read-only; `classify` makes anything else medium risk, and medium risk
    always asks.
    """
    if policy.mcp_default == "deny":
        return "deny"
    return ref.tool_approvals.get(tool) or ref.approval or policy.mcp_default


def classify(  # noqa: PLR0911 - one return per kind of tool
    tool_name: str,
    tool_input: dict[str, Any],
    policy: ApprovalPolicy,
    databases: list[DatabaseRef] | None = None,
    http: HttpRequestTool | None = None,
    read_only_mcp: frozenset[str] = frozenset(),
    mcp_modes: dict[str, ApprovalMode] | None = None,
    action_tiers: dict[str, str] | None = None,
) -> tuple[Mode, Risk]:
    if tool_name in _READ_ONLY_CAPS | _NO_ACTION_BUILTINS | _OWN_NOTES | _OWN_REQUESTS:
        return "auto", "low"

    tier = (action_tiers or {}).get(tool_name)
    if tier is not None:
        # A declared action (Phase 9.1): its tier was approved with the
        # version, and `confirm` only exists for statements pinned to the
        # asker's own rows (checked when the config was saved).
        return {"read": ("auto", "low"), "confirm": ("confirm", "medium")}.get(
            tier, ("require", "high")
        )  # type: ignore[return-value]

    if tool_name == _qual("http_request"):
        # GET and HEAD read. Anything else changes something on another
        # system, so it takes the stricter of the tool's own setting (the
        # canvas node) and the assistant-wide policy: either one can insist
        # on a human, and neither can quietly lift the other's requirement.
        method = str(tool_input.get("method") or "GET").upper()
        if method in READ_METHODS:
            return "auto", "low"
        mode = policy.http_non_get if http is None else stricter(http.approval, policy.http_non_get)
        return mode, "medium"

    if tool_name == _qual("sql_query"):
        return _classify_sql(
            str(tool_input.get("sql", "")),
            policy,
            write_exposed=_write_exposed(str(tool_input.get("connection_id", "")), databases),
        )

    if tool_name.startswith("mcp__"):  # a registered MCP server's tool (task 4.6)
        # Low risk only when the server declares the tool read-only: the one
        # case where `auto` may skip the human (plan §7.2: "auto for specific
        # read-only tools"). Anything else is medium and always asks.
        mode = (mcp_modes or {}).get(tool_name, policy.mcp_default)
        return mode, ("low" if tool_name in read_only_mcp else "medium")

    return "deny", "high"  # Bash / Write / Edit / anything unrecognised


def _write_exposed(connection_id: str, databases: list[DatabaseRef] | None) -> bool:
    """Does the canvas let *this assistant* write to this connection?

    Unknown (no database list supplied, e.g. a driver that doesn't pass one)
    is treated as exposed so the policy still gets its say — this function
    narrows when a prompt is pointless, it is not the authorisation check.
    """
    if databases is None:
        return True
    ref = next((d for d in databases if d.connection_id == connection_id), None)
    if ref is None:
        # Not a connection this assistant has at all. The handler will refuse
        # it; there is nothing for a human to usefully approve.
        return False
    return ref.expose_write


def _classify_sql(
    sql: str, policy: ApprovalPolicy, *, write_exposed: bool = True
) -> tuple[ApprovalMode, Risk]:
    """Parse to decide. A read runs unattended; a write or DDL asks.

    The permissions used here are deliberately permissive — this is *risk*
    classification, not authorisation. Whether the statement is allowed at
    all is the tool handler's job via the real connection permissions; if
    this pass refused on permissions it would deny before the model ever
    learned why.

    `write_exposed` is the one exception, and it is about the *human*, not
    the model. When the canvas has not exposed writes for this connection a
    write cannot possibly land, so prompting would interrupt someone to
    authorise something that is then refused anyway. Worse, they would be
    told afterwards that the statement they just approved did not run.
    Approval fatigue is a real failure mode: every prompt that cannot
    matter makes the prompts that do matter less likely to be read.
    """
    result, _reason = _guard_under_any_dialect(sql)
    if result is None:
        # Refused in every dialect *with every permission switched on*: no
        # human decision can make it run, because the tool handler will
        # refuse it too. Asking would be the approval-fatigue failure again —
        # a reviewer approves, then is told it was blocked — and treating it
        # as low-risk would be exactly the wrong default. So: deny, and let
        # the guard's reason reach the model via `_deny_message`.
        return "deny", "high"
    if result.kind == "ddl":
        return ("deny" if not write_exposed else policy.db_ddl), "high"
    if result.kind == "write":
        return ("deny" if not write_exposed else policy.db_write), "high"
    return "auto", "low"


#: Risk classification, not authorisation: the most permissive profile
#: possible, so the only refusals left are ones no permission could lift.
_PERMISSIVE = SqlPermissions(read=True, write=True, ddl=True, row_limit=10_000)


def _guard_under_any_dialect(sql: str) -> tuple[GuardResult | None, str]:
    """Guard `sql` as each SQL dialect in turn; the first that accepts it
    wins. Returns (None, first refusal reason) when every dialect refuses.

    The dialect is tried rather than known because the router sees only the
    tool input, not the connection's engine."""
    first_reason = ""
    for engine in ("postgres", "mysql", "sqlite"):
        try:
            return guard(sql, engine=engine, permissions=_PERMISSIVE), ""
        except SqlBlocked as exc:
            first_reason = first_reason or str(exc)
    return None, first_reason


def _deny_message(
    tool_name: str, tool_input: dict[str, Any], databases: list[DatabaseRef] | None
) -> str:
    """Say *why*, and say what would change it.

    "sql_query is not permitted for this assistant" is true and useless: it
    reads like a bug to the user and tells the model nothing, so it retries
    the same statement. Naming the switch turns both into something
    actionable.
    """
    if tool_name.startswith("mcp__") and not tool_name.startswith(f"mcp__{CAPS_SERVER_NAME}__"):
        _, server, tool = [*tool_name.split("__", 2), "", ""][:3]
        return (
            f"This assistant's rules do not allow {server}: {tool}. The rule is set on the "
            f"'{server}' MCP server node on the canvas (or turned off for all MCP tools in "
            "Approvals)."
        )
    if tool_name == _qual("http_request"):
        method = str(tool_input.get("method") or "GET").upper()
        return (
            f"This assistant may only read over HTTP (GET and HEAD), so a {method} request "
            "is refused. Change the HTTP request tool's approval setting if that is intended."
        )
    if tool_name == _qual("sql_query"):
        result, reason = _guard_under_any_dialect(str(tool_input.get("sql", "")))
        if result is None:
            # Checked first: this refusal holds whatever the switches say, so
            # pointing at 'Expose writes' would send someone to flip a toggle
            # that cannot help.
            return f"The SQL guard refused this statement: {reason}"
        if not _write_exposed(str(tool_input.get("connection_id", "")), databases):
            return (
                "This assistant can read this database but not change it. "
                "Turn on 'Expose writes' on the database node (and grant write "
                "on the connection) if that is intended."
            )
    return f"{tool_name} is not permitted for this assistant."


#: async (connection_id, "write" | "ddl") -> does the connection's *current*
#: credential allow it? Supplied by the runtime, which can reach the database.
CredentialCheck = Callable[[str, str], Awaitable[bool]]


async def _credential_refusal(
    tool_name: str, tool_input: dict[str, Any], credential_allows: CredentialCheck | None
) -> str | None:
    """Refuse, without asking anyone, a write the connection cannot make.

    `expose_write` on the node is checked at publish time against the
    connection's permission — but that permission can be switched off
    *after* publish. Without this, the router would interrupt a reviewer for
    a statement the guard is then certain to refuse.
    """
    if credential_allows is None or tool_name != _qual("sql_query"):
        return None
    result, _ = _guard_under_any_dialect(str(tool_input.get("sql", "")))
    if result is None or result.kind not in ("write", "ddl"):
        return None
    if await credential_allows(str(tool_input.get("connection_id", "")), result.kind):
        return None
    what = "writes" if result.kind == "write" else "schema changes"
    return (
        f"This database connection is read-only for {what}. Allow {what} on the "
        "connection in the Databases tab if that is intended."
    )


def build_can_use_tool(
    policy: ApprovalPolicy,
    approvals: ApprovalRequest | None = None,
    databases: list[DatabaseRef] | None = None,
    *,
    credential_allows: CredentialCheck | None = None,
    http: HttpRequestTool | None = None,
    read_only_mcp: frozenset[str] = frozenset(),
    mcp_modes: dict[str, ApprovalMode] | None = None,
    permissions: dict[str, str] | None = None,
    action_tiers: dict[str, str] | None = None,
) -> CanUseTool:
    async def decide(  # noqa: PLR0911 - one return per outcome
        tool_name: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
    ) -> tuple[PermissionResultAllow | PermissionResultDeny, str]:
        mode, risk = classify(
            tool_name, tool_input, policy, databases, http, read_only_mcp, mcp_modes, action_tiers
        )

        # `auto` skips the human for low-risk calls only (plan §4.4). A
        # medium- or high-risk call always asks, whatever a policy says: no
        # setting can make a write, a non-GET request or an MCP tool the
        # server does not declare read-only run unattended.
        if mode == "auto" and risk == "low":
            return PermissionResultAllow(), "auto"
        if mode == "deny":
            log.info("tool_denied", tool=tool_name, risk=risk)
            return PermissionResultDeny(
                message=_deny_message(tool_name, tool_input, databases)
            ), "refused"

        refusal = await _credential_refusal(tool_name, tool_input, credential_allows)
        if refusal is not None:
            log.info("tool_denied_by_credential", tool=tool_name)
            return PermissionResultDeny(message=refusal), "refused"

        if approvals is None:
            # No conversation to ask in (a dry run, or a driver that doesn't
            # support approvals). Refusing is the only safe answer.
            log.info("tool_needs_approval_unavailable", tool=tool_name, risk=risk)
            return PermissionResultDeny(
                message=(
                    f"{tool_name} needs human approval, which isn't available here. "
                    "Try a read-only alternative."
                )
            ), "refused"

        pinned = sanitize(tool_input)
        rationale = describe(tool_name, pinned)
        outcome = await approvals.request(
            tool_name,
            pinned,
            risk,
            rationale,
            tier="asker" if mode == "confirm" else "approver",
            # Links the request to the exact tool call in the trace.
            tool_use_id=getattr(ctx, "tool_use_id", None),
        )
        if outcome.decision == "queued":
            # Not run now, and not refused either: the model is told it is
            # waiting, says so, and the turn ends (no wait inside a turn).
            log.info("tool_queued", tool=tool_name, risk=risk)
            return PermissionResultDeny(message=outcome.message, interrupt=False), "queued"
        if outcome.decision == "refused":
            log.info("tool_request_refused", tool=tool_name)
            return PermissionResultDeny(message=outcome.message), "refused"
        # An eval run (task 6.1): there is nobody to ask. `interrupt` on a
        # high-risk one stops the agent rather than letting it wander toward
        # another way of doing the same thing.
        return PermissionResultDeny(
            message=outcome.message
            or "This is an automated test run, so actions that need a person's approval "
            "are declined.",
            interrupt=(risk == "high"),
        ), "unattended"

    async def can_use_tool(
        tool_name: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        result, label = await decide(tool_name, tool_input, ctx)
        # The audit trail (task 4.7): how this call came to run, or not:
        # "auto", "approved", "declined", "expired", "interrupted" or
        # "refused". Keyed by the call's id; the turn stores it with the call.
        call_id = getattr(ctx, "tool_use_id", None)
        if permissions is not None and call_id:
            permissions[str(call_id)] = label
        return result

    return can_use_tool


__all__ = [
    "ApprovalRequest",
    "CanUseTool",
    "CredentialCheck",
    "Mode",
    "RequestOutcome",
    "Requester",
    "Risk",
    "Tier",
    "build_can_use_tool",
    "classify",
    "describe",
    "mcp_mode",
    "redact",
    "sanitize",
    "stricter",
]
