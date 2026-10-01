"""The SQL guard (task 3.4).

This file is deliberately the largest test module in the project. Everything
a model writes passes through `guard()` before it reaches a customer's
database, so the cases here are attacks, not examples: stacked statements,
writes hidden behind comments or CTEs, file access smuggled into a legal
SELECT, and allow/deny bypasses via schema qualification.
"""

from __future__ import annotations

import pytest
from app.datasources.sql_guard import GuardResult, Permissions, SqlBlocked, guard

READ_ONLY = Permissions(read=True, write=False, ddl=False, row_limit=500)
WRITABLE = Permissions(read=True, write=True, ddl=False, row_limit=500)
FULL = Permissions(read=True, write=True, ddl=True, row_limit=500)


def pg(sql: str, perms: Permissions = READ_ONLY) -> GuardResult:
    return guard(sql, engine="postgres", permissions=perms)


def blocked(sql: str, perms: Permissions = READ_ONLY) -> str:
    with pytest.raises(SqlBlocked) as err:
        guard(sql, engine="postgres", permissions=perms)
    return str(err.value)


# ── the happy path ───────────────────────────────────────────


def test_a_plain_select_is_allowed_and_classified_read() -> None:
    result = pg("SELECT id, email FROM users WHERE active")
    assert result.kind == "read"
    assert result.mutating is False
    assert result.tables == ["users"]


def test_cte_and_join_reads_are_allowed() -> None:
    result = pg("WITH recent AS (SELECT * FROM orders) SELECT * FROM recent JOIN users ON true")
    assert result.kind == "read"
    assert "orders" in result.tables


def test_union_is_a_read() -> None:
    assert pg("SELECT a FROM t1 UNION SELECT a FROM t2").kind == "read"


# ── statement type gating ────────────────────────────────────


def test_write_is_refused_on_a_read_only_connection() -> None:
    assert "read-only" in blocked("UPDATE users SET email = 'x' WHERE id = 1")
    assert "read-only" in blocked("DELETE FROM users")
    assert "read-only" in blocked("INSERT INTO users (email) VALUES ('x')")


def test_write_is_allowed_once_the_permission_is_on() -> None:
    result = pg("DELETE FROM sessions WHERE expired", WRITABLE)
    assert result.kind == "write"
    assert result.mutating is True


def test_ddl_needs_its_own_permission_beyond_write() -> None:
    """`write` must not imply `ddl` — dropping a table is a different kind of
    bad day from updating a row."""
    assert "schema changes" in blocked("DROP TABLE users", WRITABLE)
    assert pg("DROP TABLE scratch", FULL).kind == "ddl"


def test_truncate_is_ddl_not_a_write() -> None:
    assert "schema changes" in blocked("TRUNCATE TABLE users", WRITABLE)


# ── the attacks ──────────────────────────────────────────────


def test_stacked_statements_are_refused() -> None:
    """The classic: a legal read with a write bolted on behind a semicolon."""
    assert "one statement" in blocked("SELECT 1; DROP TABLE users")
    assert "one statement" in blocked("SELECT 1; SELECT 2")


def test_a_write_hidden_behind_a_comment_is_still_a_write() -> None:
    """Parsing, not string matching, is what makes this work."""
    assert "read-only" in blocked("/* harmless select */ DELETE FROM users")
    assert "read-only" in blocked("--comment\nUPDATE users SET admin = true")


def test_a_write_wrapped_in_a_cte_is_still_a_write() -> None:
    """The single most dangerous false negative this module could have.

    sqlglot happens to attach the CTE as an argument on the Delete/Update
    node, so these classify correctly without special handling — but that is
    a parser-internal detail, not a guarantee, which is exactly why it is
    pinned here rather than assumed."""
    assert "read-only" in blocked("WITH x AS (SELECT 1) DELETE FROM users")
    assert "read-only" in blocked("WITH x AS (SELECT 1) UPDATE users SET a = 1")


def test_case_and_whitespace_do_not_change_classification() -> None:
    assert "read-only" in blocked("dElEtE   FROM    users")
    assert "read-only" in blocked("\n\t delete\nfrom users")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_sleep(10)",
        "SELECT lo_import('/etc/passwd')",
        "SELECT dblink('host=evil', 'SELECT 1')",
        "SELECT pg_ls_dir('/')",
    ],
)
def test_file_and_sleep_functions_are_refused_inside_a_legal_select(sql: str) -> None:
    """Every one of these is a syntactically valid read. The statement type
    check passes them; the construct check is what stops them."""
    assert "not allowed" in blocked(sql)


def test_mysql_file_writes_are_refused() -> None:
    with pytest.raises(SqlBlocked, match="filesystem"):
        guard(
            "SELECT * FROM users INTO OUTFILE '/tmp/x'",
            engine="mysql",
            permissions=READ_ONLY,
        )


def test_privilege_and_identity_changes_are_refused() -> None:
    assert "privileges" in blocked("GRANT ALL ON users TO attacker", FULL)
    assert "identity" in blocked("SET ROLE postgres", FULL)


def test_system_catalogs_cannot_be_queried_directly() -> None:
    """Introspection has its own tool; a model reading pg_catalog from
    sql_query is enumerating the server, not answering a question."""
    assert "system catalog" in blocked("SELECT * FROM pg_catalog.pg_user")
    assert "system catalog" in blocked("SELECT * FROM information_schema.tables")


def test_an_unparseable_statement_is_refused_rather_than_passed_through() -> None:
    assert "parse" in blocked("SELECT FROM WHERE ((((")


def test_empty_input_is_refused() -> None:
    assert "empty" in blocked("   ")
    assert "empty" in blocked(";")


# ── allow / deny lists ───────────────────────────────────────


def test_denied_tables_are_refused() -> None:
    perms = Permissions(read=True, deny_tables=["salaries"])
    assert "denied" in blocked("SELECT * FROM salaries", perms)


def test_a_denied_table_cannot_be_reached_by_schema_qualifying_it() -> None:
    """The check compares the bare name too, so `public.salaries` is caught
    by a deny entry of `salaries`."""
    perms = Permissions(read=True, deny_tables=["salaries"])
    assert "denied" in blocked("SELECT * FROM public.salaries", perms)


def test_a_denied_table_cannot_hide_in_a_join_or_subquery() -> None:
    perms = Permissions(read=True, deny_tables=["salaries"])
    assert "denied" in blocked("SELECT * FROM users JOIN salaries ON true", perms)
    assert "denied" in blocked("SELECT (SELECT max(x) FROM salaries) FROM users", perms)


def test_an_allow_list_is_exhaustive() -> None:
    perms = Permissions(read=True, allow_tables=["users"])
    assert pg("SELECT * FROM users", perms).kind == "read"
    assert "not in this connection's allowed tables" in blocked("SELECT * FROM orders", perms)


# ── LIMIT rewriting ──────────────────────────────────────────


def test_a_limit_is_injected_when_absent() -> None:
    result = pg("SELECT * FROM users", Permissions(read=True, row_limit=25))
    assert result.limit_applied == 25
    assert "LIMIT 25" in result.statement.upper()


def test_a_smaller_existing_limit_is_left_alone() -> None:
    result = pg("SELECT * FROM users LIMIT 5", Permissions(read=True, row_limit=25))
    assert result.limit_applied == 5
    assert "LIMIT 5" in result.statement.upper()


def test_an_oversized_limit_is_tightened() -> None:
    """The point of the cap: a model asking for a million rows gets the cap,
    not the million."""
    result = pg("SELECT * FROM users LIMIT 1000000", Permissions(read=True, row_limit=25))
    assert result.limit_applied == 25
    assert "LIMIT 25" in result.statement.upper()


def test_writes_do_not_get_a_limit_bolted_on() -> None:
    """`DELETE ... LIMIT` is not portable and changes the statement's meaning
    on engines that do accept it."""
    result = pg("DELETE FROM sessions WHERE expired", WRITABLE)
    assert result.limit_applied is None
    assert "LIMIT" not in result.statement.upper()


def test_a_cte_select_still_gets_limited() -> None:
    result = pg("WITH x AS (SELECT * FROM t) SELECT * FROM x", Permissions(read=True, row_limit=10))
    assert result.limit_applied == 10


# ── engines ──────────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["postgres", "mysql", "sqlite"])
def test_every_sql_engine_guards_the_same_way(engine: str) -> None:
    assert guard("SELECT 1 FROM t", engine=engine, permissions=READ_ONLY).kind == "read"
    with pytest.raises(SqlBlocked):
        guard("DROP TABLE t", engine=engine, permissions=READ_ONLY)


def test_mongodb_is_refused_outright() -> None:
    """Mongo has no statement to guard; reaching here with it is a bug."""
    with pytest.raises(SqlBlocked, match="does not accept SQL"):
        guard("SELECT 1", engine="mongodb", permissions=READ_ONLY)


def test_sqlite_attach_and_pragma_are_refused() -> None:
    for sql in ["ATTACH DATABASE '/etc/passwd' AS x", "PRAGMA table_info(users)"]:
        with pytest.raises(SqlBlocked):
            guard(sql, engine="sqlite", permissions=FULL)


# ── permissions parsing ──────────────────────────────────────


def test_permissions_default_to_read_only() -> None:
    perms = Permissions.from_dict({})
    assert perms.read is True
    assert perms.write is False and perms.ddl is False


def test_permissions_from_dict_lowercases_table_names() -> None:
    perms = Permissions.from_dict({"deny_tables": ["Salaries", "PUBLIC.Secrets"]})
    assert perms.deny_tables == ["salaries", "public.secrets"]
    assert "denied" in blocked("SELECT * FROM salaries", perms)


# ── regressions from the Phase 0-3 audit (P0-1 .. P0-3) ──────
#
# Every case below walked straight through the guard on a read-only
# connection. The root cause was one design flaw, not three bugs: the guard
# classified a statement by its *top-level* node only, and applied the LIMIT
# and the catalog check to one shape each. A mutation or a catalog reference
# nested anywhere below the top was invisible to it.


@pytest.mark.parametrize(
    "sql",
    [
        # A data-modifying CTE. The top-level node is a SELECT; the DELETE is
        # nested inside the WITH, and in Postgres it runs to completion even
        # if the outer SELECT returns nothing.
        "WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d",
        "WITH u AS (UPDATE users SET admin = true RETURNING *) SELECT * FROM u",
        "WITH i AS (INSERT INTO audit (a) VALUES (1) RETURNING *) SELECT count(*) FROM i",
        # Nested one level deeper, to prove the check is a walk, not a peek.
        "SELECT * FROM (WITH d AS (DELETE FROM users RETURNING id) SELECT id FROM d) AS s",
    ],
)
def test_a_write_nested_inside_a_read_is_refused(sql: str) -> None:
    """P0-1. These were classified `read`, allowed on a read-only connection,
    given a LIMIT, and — because `mutating` was False — auto-approved with
    no human involved."""
    blocked(sql)
    # Refused even with every permission on: a mutation hidden inside a read
    # is not a shape any legitimate question needs, and allowing it would
    # make what the approval card shows differ from what actually runs.
    blocked(sql, FULL)


def test_select_into_is_refused() -> None:
    """P0-1. `SELECT … INTO newtab` is Postgres's spelling of CREATE TABLE
    AS. It parses as a SELECT with a nested `Into` node."""
    assert "INTO" in blocked("SELECT * INTO newtab FROM users")
    blocked("SELECT * INTO newtab FROM users", FULL)
    with pytest.raises(SqlBlocked):
        guard("SELECT id INTO @v FROM users", engine="mysql", permissions=FULL)


def test_upserts_are_still_ordinary_writes() -> None:
    """The nested-mutation rule must not catch ON CONFLICT: sqlglot models it
    as `OnConflict`, not as a nested UPDATE, and it is a perfectly normal
    single write."""
    r = pg("INSERT INTO t (a) VALUES (1) ON CONFLICT (a) DO UPDATE SET a = 2", WRITABLE)
    assert (r.kind, r.mutating) == ("write", True)
    r = guard(
        "INSERT INTO t (a) VALUES (1) ON DUPLICATE KEY UPDATE a = 2",
        engine="mysql",
        permissions=WRITABLE,
    )
    assert r.kind == "write"


def test_ddl_sub_actions_are_not_mistaken_for_nested_mutations() -> None:
    """`ALTER TABLE … DROP COLUMN` nests a `Drop` node under the `Alter`.
    That is one DDL statement, not a DDL hidden inside another."""
    assert pg("ALTER TABLE t DROP COLUMN c", FULL).kind == "ddl"
    assert pg("CREATE TABLE t2 AS SELECT * FROM t", FULL).kind == "ddl"


def test_a_read_cte_in_front_of_a_write_is_still_allowed_with_write() -> None:
    """The direction that was always safe must keep working."""
    r = pg(
        "WITH x AS (SELECT id FROM stale) DELETE FROM t WHERE id IN (SELECT id FROM x)",
        WRITABLE,
    )
    assert (r.kind, r.mutating) == ("write", True)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT nextval('orders_id_seq')",
        "SELECT setval('orders_id_seq', 1)",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT pg_terminate_backend(123)",
        "SELECT pg_cancel_backend(123)",
        "SELECT pg_reload_conf()",
        "SELECT pg_notify('ch', 'x')",
        "SELECT lo_unlink(1)",
    ],
)
def test_side_effecting_functions_are_refused_inside_a_select(sql: str) -> None:
    """P0-1, the function-shaped variant: a write needs no DML at all if a
    function does it. `set_config('statement_timeout','0')` would also
    switch off the server-side timeout this module relies on."""
    assert "not allowed" in blocked(sql)


def test_sqlite_load_extension_is_refused() -> None:
    """Loads a native shared library into the database process."""
    with pytest.raises(SqlBlocked):
        guard("SELECT load_extension('evil')", engine="sqlite", permissions=READ_ONLY)


# P0-2 — every read gets a LIMIT, whatever its shape.


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT a FROM t1 UNION SELECT a FROM t2",
        "SELECT a FROM t1 EXCEPT SELECT a FROM t2",
        "SELECT a FROM t1 INTERSECT SELECT a FROM t2",
    ],
)
def test_set_operations_get_a_limit(sql: str) -> None:
    """P0-2. `_apply_limit` early-returned for anything that was not a bare
    SELECT, so every UNION/EXCEPT/INTERSECT ran unbounded."""
    r = pg(sql)
    assert r.limit_applied == 500
    assert r.statement.rstrip().endswith("LIMIT 500")


def test_an_oversized_limit_on_a_set_operation_is_tightened() -> None:
    r = pg("SELECT a FROM t1 UNION ALL SELECT a FROM t2 LIMIT 1000000")
    assert r.limit_applied == 500
    assert "1000000" not in r.statement


def test_a_limit_inside_one_branch_does_not_bound_the_union() -> None:
    """A LIMIT on one branch caps that branch only; the other side is still
    unbounded. Only the outer limit counts."""
    r = pg("(SELECT a FROM t1 LIMIT 1) UNION ALL (SELECT a FROM t2)")
    assert r.limit_applied == 500


@pytest.mark.parametrize("engine", ["postgres", "mysql", "sqlite"])
def test_set_operation_limits_render_in_every_dialect(engine: str) -> None:
    r = guard("SELECT a FROM t1 UNION SELECT a FROM t2", engine=engine, permissions=READ_ONLY)
    assert "LIMIT 500" in r.statement


@pytest.mark.parametrize("limit", ["-1", "'-1'", "'-100'"])
def test_sqlite_negative_limit_does_not_mean_unlimited(limit: str) -> None:
    """In SQLite `LIMIT -1` means *no limit*. It must be replaced, never
    accepted as 'already smaller than the cap'.

    The quoted form is the one that actually got through: `-1` parses as
    `Neg(1)` and failed `int()` anyway, but `'-1'` is a string literal,
    `int('-1')` is -1, and -1 <= row_limit. SQLite coerces it and returns
    every row (verified: 1000 of 1000)."""
    r = guard(f"SELECT * FROM t LIMIT {limit}", engine="sqlite", permissions=READ_ONLY)
    assert r.limit_applied == 500
    assert "-1" not in r.statement


def test_fetch_first_is_replaced_not_stacked() -> None:
    r = pg("SELECT * FROM t FETCH FIRST 100000 ROWS ONLY")
    assert r.limit_applied == 500
    assert "FETCH" not in r.statement.upper()


# P0-3 — system catalogs, however they are spelled.


@pytest.mark.parametrize(
    "table",
    [
        "pg_roles",
        "pg_user",
        "pg_shadow",
        "pg_authid",
        "pg_settings",
        "pg_stat_activity",
        "pg_class",
    ],
)
def test_unqualified_postgres_catalogs_are_refused(table: str) -> None:
    """P0-3. Postgres searches `pg_catalog` implicitly and *first*, so an
    unqualified `pg_roles` IS `pg_catalog.pg_roles`. Only the qualified
    spelling used to be caught."""
    assert "system catalog" in blocked(f"SELECT * FROM {table}")


def test_a_catalog_cannot_hide_in_a_subquery_or_join() -> None:
    assert "system catalog" in blocked("SELECT * FROM users WHERE id IN (SELECT oid FROM pg_roles)")
    assert "system catalog" in blocked("SELECT * FROM users JOIN pg_user ON true")


def test_any_pg_schema_is_a_system_schema() -> None:
    assert "system catalog" in blocked("SELECT * FROM pg_toast.pg_toast_1234")


def test_a_tenant_table_in_a_real_schema_is_not_mistaken_for_a_catalog() -> None:
    """The prefix rule is about name *resolution*: a qualified
    `public.pg_notes` cannot resolve to pg_catalog, so it stays legal."""
    assert pg("SELECT * FROM public.pg_notes").kind == "read"


def test_pg_prefix_rule_is_postgres_only() -> None:
    """MySQL resolves unqualified names against the connected database only."""
    r = guard("SELECT * FROM pg_notes", engine="mysql", permissions=READ_ONLY)
    assert r.kind == "read"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM sqlite_schema",
        "SELECT * FROM sqlite_temp_schema",
        "SELECT * FROM main.sqlite_master",
        "SELECT * FROM sqlite_sequence",
        "SELECT * FROM pragma_table_info('users')",
    ],
)
def test_sqlite_internals_are_refused(sql: str) -> None:
    """`sqlite_schema` is the modern alias of `sqlite_master` and was not
    listed; `pragma_*` table-valued functions dodge the PRAGMA text check
    because `_` is a word character, so `\\bPRAGMA\\b` never matches."""
    with pytest.raises(SqlBlocked):
        guard(sql, engine="sqlite", permissions=READ_ONLY)


# ── the security pass (task 6.3) ─────────────────────────────


def _blocked(sql: str, *, engine: str = "postgres", **perms: object) -> str:
    with pytest.raises(SqlBlocked) as why:
        guard(sql, engine=engine, permissions=Permissions(**perms))  # type: ignore[arg-type]
    return str(why.value)


def _allowed(sql: str, *, engine: str = "postgres", **perms: object) -> str:
    return guard(sql, engine=engine, permissions=Permissions(**perms)).statement  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "sql",
    [
        # A quoted, schema-qualified name kept its quotes when compared with
        # the list, so it never matched, and Postgres ran the real function.
        "SELECT pg_catalog.\"pg_read_file\"('/etc/passwd')",
        'SELECT pg_catalog."pg_sleep"(100)',
        "SELECT pg_catalog.\"lo_import\"('/etc/passwd')",
        "SELECT pg_catalog.\"nextval\"('s')",
        'SELECT "pg_read_file"(1)',
        "SELECT PG_CATALOG.PG_READ_FILE('/etc/passwd')",
    ],
)
def test_a_quoted_or_qualified_function_name_is_still_the_same_function(sql: str) -> None:
    assert "is not allowed" in _blocked(sql)


@pytest.mark.parametrize(
    "sql",
    [
        # Siblings of names that were on the list.
        "SELECT lowrite(0, 'abc')",
        "SELECT lo_truncate(0, 0)",
        "SELECT lo_get(1)",
        "SELECT lo_open(1, 131072)",
        "SELECT pg_stat_reset()",
        "SELECT pg_logical_emit_message(true, 'a', 'b')",
        "SELECT pg_create_restore_point('x')",
        "SELECT pg_drop_replication_slot('x')",
        "SELECT pg_advisory_lock(1)",
        "SELECT dblink_connect_u('host=internal')",
        "SELECT dblink_send_query('c', 'delete from x')",
        # What the server is, and who else is on it.
        "SELECT * FROM pg_stat_get_activity(NULL)",
        "SELECT current_setting('data_directory')",
        "SELECT pg_ls_logdir()",
        "SELECT inet_server_addr()",
    ],
)
def test_whole_function_families_are_refused_not_just_the_listed_names(sql: str) -> None:
    assert "is not allowed" in _blocked(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT table_to_xml('secret', true, false, '')",
        "SELECT table_to_xml_and_xmlschema('secret', true, false, '')",
        "SELECT query_to_xml_and_xmlschema('select * from secret', true, false, '')",
        "SELECT schema_to_xml('public', true, false, '')",
        "SELECT database_to_xml(true, false, '')",
        "SELECT cursor_to_xml('c', 10, true, false, '')",
    ],
)
def test_a_denied_table_cannot_be_dumped_through_a_function_that_takes_its_name(
    sql: str,
) -> None:
    """The table is named inside a string, so the table checks never see it."""
    assert "is not allowed" in _blocked(sql, deny_tables=["secret"])


def test_ordinary_functions_still_work() -> None:
    sql = "SELECT count(*), lower(name), coalesce(a, b), now(), pg_typeof(id) FROM orders"
    assert "COUNT(*)" in _allowed(sql)
    assert "PG_SIZE_PRETTY" in _allowed("SELECT pg_size_pretty(10)")


def test_a_read_cannot_take_row_locks() -> None:
    for tail in ("FOR UPDATE", "FOR UPDATE NOWAIT", "FOR SHARE"):
        assert "cannot lock rows" in _blocked(f"SELECT * FROM orders {tail}")


def test_a_schema_qualified_deny_entry_also_stops_the_bare_name() -> None:
    assert "denied" in _blocked("SELECT * FROM secret", deny_tables=["public.secret"])
    assert "denied" in _blocked("SELECT * FROM public.secret", deny_tables=["public.secret"])
    assert "denied" in _blocked("SELECT * FROM public.secret", deny_tables=["secret"])
    assert "denied" in _blocked("SELECT * FROM other.secret", deny_tables=["secret"])
    # A different schema's table of the same name is a different table.
    assert "other.secret" in _allowed("SELECT * FROM other.secret", deny_tables=["public.secret"])


def test_a_bare_allow_entry_does_not_admit_another_schemas_table() -> None:
    assert "not in this connection's allowed tables" in _blocked(
        "SELECT * FROM evil.orders", allow_tables=["orders"]
    )
    # The default schema, spelled out or not, is the same table.
    assert _allowed("SELECT * FROM orders", allow_tables=["orders"])
    assert _allowed("SELECT * FROM public.orders", allow_tables=["orders"])
    assert _allowed("SELECT * FROM orders", allow_tables=["public.orders"])
    assert _allowed("SELECT * FROM main.orders", engine="sqlite", allow_tables=["orders"])
    assert _allowed("SELECT * FROM evil.orders", allow_tables=["evil.orders"])
    # MySQL's default is the connected database, which the guard doesn't
    # know: a qualified table has to be listed qualified.
    assert "not in this connection" in _blocked(
        "SELECT * FROM shop.orders", engine="mysql", allow_tables=["orders"]
    )
    assert _allowed("SELECT * FROM shop.orders", engine="mysql", allow_tables=["shop.orders"])
