"""Human-in-the-loop approvals (task 3.8).

Two layers: the classifier (does this even need a human?) and the round trip
(a tool call blocks, an event reaches the client, a decision unblocks it).
The property worth pinning hardest is the *default*: every path that isn't an
explicit "approved" must end in a denial — timeouts included.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from app.agent import approval_registry as registry
from app.agent.approvals import ApprovalRequest, build_can_use_tool, classify, describe, redact
from app.schemas.assistant_config import ApprovalPolicy, DatabaseRef
from claude_agent_sdk import PermissionResultDeny

POLICY = ApprovalPolicy()


def q(name: str) -> str:
    return f"mcp__caps__{name}"


# ── classification ───────────────────────────────────────────


@pytest.mark.parametrize(
    "tool",
    [
        "calculator",
        "datetime",
        "kb_search",
        "kb_list_sources",
        "sql_list_schemas",
        "sql_introspect",
        "mongo_find",
        "mongo_aggregate",
    ],
)
def test_read_only_tools_run_unattended(tool: str) -> None:
    assert classify(q(tool), {}, POLICY) == ("auto", "low")


def test_a_select_does_not_ask_for_a_human() -> None:
    """The whole point of parsing rather than keying on the tool name: a
    prompt on every read trains people to click approve without reading."""
    mode, risk = classify(q("sql_query"), {"sql": "SELECT count(*) FROM users"}, POLICY)
    assert (mode, risk) == ("auto", "low")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users",
        "UPDATE users SET admin = true",
        "INSERT INTO audit (x) VALUES (1)",
    ],
)
def test_a_write_asks_for_a_human(sql: str) -> None:
    mode, risk = classify(q("sql_query"), {"sql": sql}, POLICY)
    assert mode == "require"
    assert risk == "high"


def test_ddl_is_classified_by_its_own_policy_knob() -> None:
    mode, risk = classify(q("sql_query"), {"sql": "DROP TABLE users"}, POLICY)
    assert (mode, risk) == (POLICY.db_ddl, "high")


def test_a_cte_wrapped_write_still_asks() -> None:
    mode, _ = classify(q("sql_query"), {"sql": "WITH x AS (SELECT 1) DELETE FROM t"}, POLICY)
    assert mode == "require"


def test_unparseable_sql_is_treated_as_dangerous_not_safe() -> None:
    """ "I couldn't understand this" must not mean "probably a read".

    It is denied rather than escalated: the guard refuses it under every
    dialect with every permission on, so no human decision could make it
    run, and asking would only teach reviewers that approving is
    meaningless."""
    mode, risk = classify(q("sql_query"), {"sql": "!!! not sql !!!"}, POLICY)
    assert mode == "deny"
    assert risk == "high"


def test_subagent_delegation_is_not_itself_gated() -> None:
    """The subagent's own tool calls come back through the router one by
    one; the act of delegating is not an action. Whether `Agent` exists at
    all is decided by `options.builtin_tools` and the PreToolUse gate."""
    assert classify("Agent", {"prompt": "find x"}, POLICY) == ("auto", "low")


def test_unknown_tools_are_denied_outright() -> None:
    assert classify("Bash", {"command": "rm -rf /"}, POLICY) == ("deny", "high")
    assert classify("Write", {}, POLICY) == ("deny", "high")


# ── what the reviewer sees ───────────────────────────────────


def test_the_card_shows_the_exact_statement() -> None:
    """ "Approve a database write?" is unanswerable; the statement is."""
    sql = "DELETE FROM sessions WHERE expired_at < now()"
    assert describe(q("sql_query"), {"sql": sql}) == sql


def test_credentials_are_redacted_before_display_or_logging() -> None:
    dirty = {
        "password": "hunter2",
        "nested": {"api_key": "sk-123", "keep": "visible"},
        "list": [{"token": "t"}],
    }
    clean = redact(dirty)
    assert clean["password"] == "<redacted>"
    assert clean["nested"]["api_key"] == "<redacted>"
    assert clean["nested"]["keep"] == "visible"
    assert clean["list"][0]["token"] == "<redacted>"
    assert "hunter2" not in str(clean)


# ── the round trip ───────────────────────────────────────────


async def _decide(tool: str, sql: str, answer: str | None, *, delay: float = 0.0) -> object:
    """Run `can_use_tool` against a stub reviewer that answers `answer`
    (or never answers, when None)."""
    seen: dict[str, object] = {}

    async def request(
        tool_name: str, tool_input: dict, risk: str, rationale: str, **_k: object
    ) -> str:
        seen.update(tool=tool_name, risk=risk, rationale=rationale)
        if delay:
            await asyncio.sleep(delay)
        if answer is None:
            await asyncio.sleep(3600)
        return answer

    can_use = build_can_use_tool(
        POLICY,
        ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=request),
    )
    result = await can_use(q(tool), {"sql": sql}, None)  # type: ignore[arg-type]
    return result, seen


async def test_an_approval_lets_the_tool_run() -> None:
    result, seen = await _decide("sql_query", "DELETE FROM t", "approved")
    assert type(result).__name__ == "PermissionResultAllow"
    assert seen["risk"] == "high"
    assert seen["rationale"] == "DELETE FROM t"


async def test_a_denial_stops_the_tool_and_says_a_human_declined() -> None:
    result, _ = await _decide("sql_query", "DELETE FROM t", "denied")
    assert type(result).__name__ == "PermissionResultDeny"
    assert "declined" in result.message
    # High-risk denials interrupt rather than let the agent try another route.
    assert result.interrupt is True


async def test_an_expiry_denies_rather_than_allows() -> None:
    """The most important default in this module."""
    result, _ = await _decide("sql_query", "DELETE FROM t", "expired")
    assert type(result).__name__ == "PermissionResultDeny"
    assert "in time" in result.message


async def test_without_a_reviewer_anything_needing_one_is_denied() -> None:
    """A dry run or a driver with no conversation must not auto-allow."""
    can_use = build_can_use_tool(POLICY, None)
    result = await can_use(q("sql_query"), {"sql": "DROP TABLE t"}, None)  # type: ignore[arg-type]
    assert type(result).__name__ == "PermissionResultDeny"
    assert "isn't available" in result.message


async def test_a_read_never_reaches_the_reviewer_at_all() -> None:
    called = False

    async def request(*_a: object, **_k: object) -> str:  # pragma: no cover
        nonlocal called
        called = True
        return "approved"

    can_use = build_can_use_tool(
        POLICY,
        ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=request),
    )
    await can_use(q("sql_query"), {"sql": "SELECT 1"}, None)  # type: ignore[arg-type]
    assert called is False


# ── the waiting-room registry ────────────────────────────────


async def test_register_then_resolve_wakes_the_waiter() -> None:
    aid = uuid.uuid4()
    future = registry.register(aid)
    assert registry.resolve_local(aid, "approved") is True
    assert await future == "approved"


async def test_a_decision_arriving_before_the_await_is_not_lost() -> None:
    """Why `register()` is called before the approval is announced."""
    aid = uuid.uuid4()
    future = registry.register(aid)
    registry.resolve_local(aid, "denied")  # resolved while nobody is awaiting
    assert await registry.wait(aid, future, timeout=1.0) == "denied"


async def test_waiting_times_out_to_expired() -> None:
    aid = uuid.uuid4()
    future = registry.register(aid)
    assert await registry.wait(aid, future, timeout=0.05) == "expired"
    # ...and the slot is released rather than leaking for the process's life.
    assert registry.pending_count() == 0


async def test_resolving_something_nobody_is_waiting_for_is_not_an_error() -> None:
    """Normal when the turn already timed out, or the decision landed on a
    different worker."""
    assert registry.resolve_local(uuid.uuid4(), "approved") is False


async def test_a_second_decision_does_not_overwrite_the_first() -> None:
    aid = uuid.uuid4()
    future = registry.register(aid)
    assert registry.resolve_local(aid, "approved") is True
    assert registry.resolve_local(aid, "denied") is False
    assert await future == "approved"


# ── the node gate comes before the human (regression) ────────
#
# Found by driving the real demo path: an UPDATE against a connection whose
# canvas node had `expose_write=False` still stopped and asked a human. The
# reviewer approved it and was then told "blocked: this connection is
# read-only". Interrupting someone to authorise something that cannot happen
# is worse than not asking: it is the exact mechanism by which people learn
# to click approve without reading.


def _db(connection_id: str, *, expose_write: bool) -> DatabaseRef:
    return DatabaseRef(connection_id=connection_id, expose_write=expose_write)


@pytest.mark.parametrize("sql", ["UPDATE t SET a=1", "DROP TABLE t", "DELETE FROM t"])
def test_a_write_the_canvas_never_exposed_is_denied_not_escalated(sql: str) -> None:
    mode, risk = classify(
        q("sql_query"), {"connection_id": "c1", "sql": sql}, POLICY, [_db("c1", expose_write=False)]
    )
    assert mode == "deny"
    assert risk == "high"


@pytest.mark.parametrize("sql", ["UPDATE t SET a=1", "DROP TABLE t"])
def test_the_same_write_does_ask_once_the_node_exposes_writes(sql: str) -> None:
    mode, risk = classify(
        q("sql_query"), {"connection_id": "c1", "sql": sql}, POLICY, [_db("c1", expose_write=True)]
    )
    assert mode == "require"
    assert risk == "high"


def test_reads_are_unaffected_by_the_write_gate() -> None:
    """The gate must narrow *writes* only — clamping reads would break the
    ordinary case for every read-only connection, which is most of them."""
    mode, risk = classify(
        q("sql_query"),
        {"connection_id": "c1", "sql": "SELECT 1"},
        POLICY,
        [_db("c1", expose_write=False)],
    )
    assert (mode, risk) == ("auto", "low")


def test_a_connection_this_assistant_does_not_have_is_denied() -> None:
    mode, _ = classify(
        q("sql_query"),
        {"connection_id": "someone-elses", "sql": "UPDATE t SET a=1"},
        POLICY,
        [_db("c1", expose_write=True)],
    )
    assert mode == "deny"


def test_omitting_the_database_list_still_escalates() -> None:
    """Callers that pass no list (a driver without one) must not accidentally
    get *fewer* prompts — unknown has to mean "ask", not "allow silently"."""
    mode, _ = classify(q("sql_query"), {"connection_id": "c1", "sql": "UPDATE t SET a=1"}, POLICY)
    assert mode == "require"


def test_unparseable_sql_is_denied_when_writes_are_not_exposed() -> None:
    mode, risk = classify(
        q("sql_query"),
        {"connection_id": "c1", "sql": ")))not sql((("},
        POLICY,
        [_db("c1", expose_write=False)],
    )
    assert (mode, risk) == ("deny", "high")


async def test_the_refusal_names_the_switch_that_would_allow_it() -> None:
    """A refusal the user cannot act on gets reported as a bug, and a model
    that isn't told why simply retries the same statement."""
    can_use = build_can_use_tool(POLICY, None, [_db("c1", expose_write=False)])
    result = await can_use(q("sql_query"), {"connection_id": "c1", "sql": "UPDATE t SET a=1"}, None)
    assert isinstance(result, PermissionResultDeny)
    assert "Expose writes" in result.message
    assert "read this database but not change it" in result.message


async def test_an_ordinary_refusal_keeps_the_generic_message() -> None:
    can_use = build_can_use_tool(POLICY, None, [_db("c1", expose_write=True)])
    result = await can_use("Bash", {"command": "rm -rf /"}, None)
    assert isinstance(result, PermissionResultDeny)
    assert "not permitted" in result.message


# ── statements the guard refuses never reach a human ─────────
#
# Knock-on from the P0-1 guard fix: a data-modifying CTE is now refused by
# the guard in every dialect. The router's old fallback for "refused
# everywhere" was to *ask*, so the fix would have reintroduced the
# approve-then-blocked failure. These pin the fallback to deny.


@pytest.mark.parametrize(
    "sql",
    [
        "WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d",
        "SELECT * INTO newtab FROM users",
        "SELECT pg_terminate_backend(1)",
    ],
)
async def test_a_guard_refused_statement_is_denied_without_asking(sql: str) -> None:
    asked: list[str] = []

    async def request(_tool: str, _input: dict, _risk: str, _why: str, **_k: object) -> str:
        asked.append(_why)
        return "approved"

    approvals = ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=request)
    can_use = build_can_use_tool(POLICY, approvals, [_db("c1", expose_write=True)])
    result = await can_use(q("sql_query"), {"connection_id": "c1", "sql": sql}, None)

    assert asked == [], "a human was interrupted for a statement that can never run"
    assert isinstance(result, PermissionResultDeny)
    assert result.message.startswith("The SQL guard refused this statement:")


async def test_the_guard_reason_wins_over_the_expose_writes_hint() -> None:
    """Pointing at 'Expose writes' would send someone to flip a toggle that
    cannot help: the guard refuses this whatever the switches say."""
    can_use = build_can_use_tool(POLICY, None, [_db("c1", expose_write=False)])
    result = await can_use(
        q("sql_query"),
        {"connection_id": "c1", "sql": "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d"},
        None,
    )
    assert isinstance(result, PermissionResultDeny)
    assert "nested inside another statement" in result.message
    assert "Expose writes" not in result.message


async def test_a_legitimate_write_still_asks() -> None:
    """The deny path must stay narrow: an ordinary UPDATE still escalates."""
    asked: list[str] = []

    async def request(_tool: str, _input: dict, _risk: str, why: str, **_k: object) -> str:
        asked.append(why)
        return "denied"

    approvals = ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=request)
    can_use = build_can_use_tool(POLICY, approvals, [_db("c1", expose_write=True)])
    await can_use(q("sql_query"), {"connection_id": "c1", "sql": "UPDATE t SET a = 1"}, None)
    assert asked == ["UPDATE t SET a = 1"]
