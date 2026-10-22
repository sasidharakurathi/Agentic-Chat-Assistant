"""Human-in-the-loop approvals (task 3.8; Phase 9.1).

Two layers: the classifier (does this even need a person?) and the hand-off
(since Phase 9.1 a call that needs one is stored as a request and the turn
goes on: nothing waits inside it). The property worth pinning hardest is
the *default*: no path runs a call that needs a person, in the turn.
"""

from __future__ import annotations

import uuid

import pytest
from app.agent.approvals import (
    ApprovalRequest,
    RequestOutcome,
    build_can_use_tool,
    classify,
    describe,
    redact,
)
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


# ── the hand-off (Phase 9.1) ─────────────────────────────────


async def _decide(
    tool: str, sql: str, outcome: RequestOutcome
) -> tuple[object, dict[str, object], dict[str, str]]:
    """Run `can_use_tool` against a stub that stores the request and says
    `outcome`. Returns the result, what the stub saw, and the labels."""
    seen: dict[str, object] = {}
    labels: dict[str, str] = {}

    async def request(
        tool_name: str, tool_input: dict, risk: str, rationale: str, **k: object
    ) -> RequestOutcome:
        seen.update(tool=tool_name, risk=risk, rationale=rationale, **k)
        return outcome

    can_use = build_can_use_tool(
        POLICY,
        ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=request),
        permissions=labels,
    )

    class Ctx:
        tool_use_id = "call-1"

    result = await can_use(q(tool), {"sql": sql}, Ctx())  # type: ignore[arg-type]
    return result, seen, labels


async def test_a_write_is_stored_and_the_turn_goes_on() -> None:
    """Nothing runs in the turn and nothing waits: the model is told it is
    saved, and is not interrupted, so it can tell the person."""
    result, seen, labels = await _decide(
        "sql_query", "DELETE FROM t", RequestOutcome("queued", "Saved as request R-1.")
    )
    assert isinstance(result, PermissionResultDeny)
    assert result.message == "Saved as request R-1."
    assert result.interrupt is False
    assert seen["risk"] == "high"
    assert seen["rationale"] == "DELETE FROM t"
    assert seen["tier"] == "approver", "a free-form write is an approver's"
    assert seen["tool_use_id"] == "call-1"
    assert labels == {"call-1": "queued"}


async def test_a_request_that_cant_be_stored_says_why() -> None:
    result, _, labels = await _decide(
        "sql_query", "DELETE FROM t", RequestOutcome("refused", "The values don't fit.")
    )
    assert isinstance(result, PermissionResultDeny)
    assert result.message == "The values don't fit."
    assert labels == {"call-1": "refused"}


async def test_an_eval_declines_what_needs_a_person() -> None:
    result, _, labels = await _decide("sql_query", "DELETE FROM t", RequestOutcome("unattended"))
    assert isinstance(result, PermissionResultDeny)
    assert "automated test run" in result.message
    # High risk stops the agent rather than letting it find another route.
    assert result.interrupt is True
    assert labels == {"call-1": "unattended"}


async def test_without_a_reviewer_anything_needing_one_is_denied() -> None:
    """A dry run or a driver with no conversation must not auto-allow."""
    can_use = build_can_use_tool(POLICY, None)
    result = await can_use(q("sql_query"), {"sql": "DROP TABLE t"}, None)  # type: ignore[arg-type]
    assert type(result).__name__ == "PermissionResultDeny"
    assert "isn't available" in result.message


async def test_a_read_never_reaches_the_reviewer_at_all() -> None:
    called = False

    async def request(*_a: object, **_k: object) -> RequestOutcome:  # pragma: no cover
        nonlocal called
        called = True
        return RequestOutcome("queued")

    can_use = build_can_use_tool(
        POLICY,
        ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=request),
    )
    await can_use(q("sql_query"), {"sql": "SELECT 1"}, None)  # type: ignore[arg-type]
    assert called is False


# ── declared actions (Phase 9.1) ─────────────────────────────


@pytest.mark.parametrize(
    ("tier", "mode", "risk"),
    [("read", "auto", "low"), ("confirm", "confirm", "medium"), ("approver", "require", "high")],
)
def test_a_declared_action_runs_as_its_tier_says(tier: str, mode: str, risk: str) -> None:
    tiers = {q("act_request_access"): tier}
    assert classify(q("act_request_access"), {}, POLICY, action_tiers=tiers) == (mode, risk)


async def test_a_confirm_action_goes_to_the_asker_and_an_approver_one_does_not() -> None:
    seen: list[object] = []

    async def request(*_a: object, **k: object) -> RequestOutcome:
        seen.append(k["tier"])
        return RequestOutcome("queued", "saved")

    approvals = ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=request)
    can_use = build_can_use_tool(
        POLICY,
        approvals,
        action_tiers={q("act_mine"): "confirm", q("act_theirs"): "approver"},
    )
    await can_use(q("act_mine"), {}, None)  # type: ignore[arg-type]
    await can_use(q("act_theirs"), {}, None)  # type: ignore[arg-type]
    assert seen == ["asker", "approver"]


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

    async def request(
        _tool: str, _input: dict, _risk: str, _why: str, **_k: object
    ) -> RequestOutcome:
        asked.append(_why)
        return RequestOutcome("queued")

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
    """The deny path must stay narrow: an ordinary UPDATE is still stored
    for a person to decide."""
    asked: list[str] = []

    async def request(
        _tool: str, _input: dict, _risk: str, why: str, **_k: object
    ) -> RequestOutcome:
        asked.append(why)
        return RequestOutcome("queued")

    approvals = ApprovalRequest(conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), request=request)
    can_use = build_can_use_tool(POLICY, approvals, [_db("c1", expose_write=True)])
    await can_use(q("sql_query"), {"connection_id": "c1", "sql": "UPDATE t SET a = 1"}, None)
    assert asked == ["UPDATE t SET a = 1"]
