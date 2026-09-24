"""The SQL guard (plan §6.3) — the component where a miss is a breach.

Everything a model produces passes through here before it reaches a tenant's
database. Three jobs, in order:

1. **Parse and classify.** Exactly one statement, of a type this connection's
   permissions allow. Parsing with `sqlglot` rather than matching strings is
   the whole point: `/*x*/DELETE` and `DEL/**/ETE` and `delete` are all the
   same to a parser and all different to a regex.
2. **Refuse dangerous constructs**, even inside an otherwise-legal SELECT —
   file access (`pg_read_file`, `COPY … TO`, `INTO OUTFILE`), sleeps, role
   changes, `dblink`, catalog rummaging.
3. **Rewrite.** Force a `LIMIT` no larger than the connection's `row_limit`
   onto top-level SELECTs, so "show me the orders table" can't stream ten
   million rows into a model's context.

**Design stance: allow-list, not block-list, for the thing that matters.**
Statement *types* are allow-listed — anything sqlglot doesn't parse into a
recognised, permitted expression is refused. The block-lists below are a
second layer for constructs that hide inside a permitted statement type, not
the primary defence. A block-list alone is a losing game; a block-list
behind an allow-list is defence in depth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

import sqlglot
from sqlglot import exp

#: sqlglot dialect per engine.
DIALECTS = {"postgres": "postgres", "mysql": "mysql", "sqlite": "sqlite"}

#: Always allowed when `read` is on.
READ_TYPES = (exp.Select, exp.Union, exp.Except, exp.Intersect)

#: Allowed only with `write`.
WRITE_TYPES = (exp.Insert, exp.Update, exp.Delete)

#: Allowed only with `ddl`.
DDL_TYPES = (exp.Create, exp.Alter, exp.Drop, exp.TruncateTable)

#: Nodes that change data or schema. Each is legitimate only as the *top-level*
#: node of a statement, where it is classified and permission-checked. Found
#: anywhere below the top, it is a mutation hiding inside something else.
NESTED_MUTATION_TYPES = (*WRITE_TYPES, exp.Merge, *DDL_TYPES, exp.Command)

#: Function names that read/write the server's filesystem, sleep, reach the
#: network, or change server state as a side effect. Legal inside a SELECT,
#: which is exactly why they are listed: a write needs no DML at all if a
#: function performs it.
BANNED_FUNCTIONS = frozenset(
    {
        # filesystem
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_file_write",
        "pg_file_unlink",
        "pg_file_rename",
        "lo_import",
        "lo_export",
        "load_file",
        "readfile",
        "writefile",
        # sleeps / resource exhaustion
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "sleep",
        "benchmark",
        # network / code execution
        "dblink",
        "dblink_exec",
        "dblink_connect",
        "query_to_xml",
        "sys_exec",
        "sys_eval",
        "xp_cmdshell",
        "load_extension",
        # state-changing side effects
        "nextval",
        "setval",
        "set_config",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_switch_wal",
        "pg_promote",
        "pg_notify",
        "lo_unlink",
        "lo_create",
        "lo_creat",
        "lo_put",
        "lo_from_bytea",
    }
)

#: SQLite's table-valued PRAGMA functions (`pragma_table_info(...)`) slip
#: past the `\bPRAGMA\b` text check, because `_` is a word character and the
#: boundary never matches. Refused by prefix.
BANNED_FUNCTION_PREFIXES = ("pragma_",)

#: Catalog/system schemas. Introspection has its own dedicated path
#: (`sql_introspect`); a model reaching into these from `sql_query` is
#: enumerating the server, not answering a question.
BANNED_SCHEMAS = frozenset(
    {
        "pg_catalog",
        "information_schema",
        "pg_toast",
        "mysql",
        "performance_schema",
        "sys",
        "sqlite_master",
        "sqlite_temp_master",
    }
)

#: Raw-text constructs sqlglot may parse into something innocuous-looking, or
#: that only appear as statement suffixes. Checked against the *original*
#: text, so this is the one place a string match is appropriate.
BANNED_PATTERNS = (
    (re.compile(r"\bINTO\s+OUTFILE\b", re.I), "INTO OUTFILE writes to the server's filesystem"),
    (re.compile(r"\bINTO\s+DUMPFILE\b", re.I), "INTO DUMPFILE writes to the server's filesystem"),
    (re.compile(r"\bSET\s+ROLE\b", re.I), "SET ROLE changes the effective identity"),
    (re.compile(r"\bSET\s+SESSION\s+AUTHORIZATION\b", re.I), "SET SESSION AUTHORIZATION"),
    (re.compile(r"\bGRANT\b", re.I), "GRANT changes privileges"),
    (re.compile(r"\bREVOKE\b", re.I), "REVOKE changes privileges"),
    (re.compile(r"\bCOPY\b.*\b(FROM|TO)\b\s*(PROGRAM|')", re.I), "COPY reads or writes files"),
    (re.compile(r"\bLOAD\s+DATA\b", re.I), "LOAD DATA reads files"),
    (re.compile(r"\bATTACH\b", re.I), "ATTACH opens another database file"),
    (re.compile(r"\bPRAGMA\b", re.I), "PRAGMA changes engine behaviour"),
)


class SqlBlocked(Exception):
    """The statement is not permitted. The message is shown to the model, so
    it says *why* — a blocked agent that knows the reason can try a legal
    alternative instead of retrying the same thing."""


Kind = str  # "read" | "write" | "ddl"


@dataclass
class GuardResult:
    statement: str
    kind: Kind
    tables: list[str] = field(default_factory=list)
    limit_applied: int | None = None
    #: True when the statement changes data — what the approval router keys on.
    mutating: bool = False
    #: True when a mutating statement also yields rows (Postgres/SQLite
    #: ``RETURNING``). Adapters need this because the two cases are executed
    #: differently: a plain write reports a row *count* from the driver, while
    #: a RETURNING write is fetched like a read and counts what came back.
    returns_rows: bool = False


@dataclass
class Permissions:
    read: bool = True
    write: bool = False
    ddl: bool = False
    allow_tables: list[str] = field(default_factory=list)
    deny_tables: list[str] = field(default_factory=list)
    row_limit: int = 500
    statement_timeout_ms: int = 10_000

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Permissions:
        raw = raw or {}
        return cls(
            read=bool(raw.get("read", True)),
            write=bool(raw.get("write", False)),
            ddl=bool(raw.get("ddl", False)),
            allow_tables=[str(t).lower() for t in raw.get("allow_tables", [])],
            deny_tables=[str(t).lower() for t in raw.get("deny_tables", [])],
            row_limit=int(raw.get("row_limit", 500)),
            statement_timeout_ms=int(raw.get("statement_timeout_ms", 10_000)),
        )


def _classify(tree: exp.Expression) -> Kind:
    """Classify by the top-level node. Only half the job: a top-level node
    says nothing about what is nested beneath it, which is what
    `_check_nested_mutations` is for. The two must always run together."""
    if isinstance(tree, exp.With):
        # Version-defensive: this sqlglot attaches a CTE as an argument on the
        # statement node rather than wrapping it, so today this never fires.
        inner = tree.this
        if inner is not None:
            return _classify(inner)
    if isinstance(tree, DDL_TYPES):
        return "ddl"
    if isinstance(tree, WRITE_TYPES):
        return "write"
    if isinstance(tree, READ_TYPES):
        return "read"
    raise SqlBlocked(f"{type(tree).__name__.upper()} statements are not allowed")


def _check_nested_mutations(tree: exp.Expression) -> None:
    """Refuse any mutation that is not the top-level node.

    The top-level classification alone is blind to this:

        WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d

    is a SELECT at the top, so it classified as a read — allowed on a
    read-only connection, given a LIMIT, and auto-approved because
    `mutating` was False. Postgres runs a data-modifying CTE to completion
    regardless of what the outer SELECT returns, so the LIMIT bounded
    nothing.

    Refused outright rather than reclassified as a write. No legitimate
    question needs a mutation hidden inside a read, and allowing it with
    `write` on would make the statement a reviewer approves look like a read.
    Issue the DELETE on its own and it goes through the normal write path.

    One exception: DDL sub-actions under a DDL statement. `ALTER TABLE t
    DROP COLUMN c` nests a `Drop` under the `Alter`; that is one statement.
    """
    top_is_ddl = isinstance(tree, DDL_TYPES)
    for node in tree.walk():
        if node is tree:
            continue
        if isinstance(node, exp.Into):
            # `SELECT … INTO newtab` is Postgres's spelling of CREATE TABLE AS;
            # in MySQL it assigns session variables. Neither is a read.
            raise SqlBlocked(
                "SELECT … INTO is not allowed: it creates a table or sets variables. "
                "Use CREATE TABLE … AS SELECT if a new table is really intended."
            )
        if top_is_ddl and isinstance(node, DDL_TYPES):
            continue
        if isinstance(node, NESTED_MUTATION_TYPES):
            name = type(node).__name__.upper()
            raise SqlBlocked(
                f"a {name} nested inside another statement is not allowed; "
                "issue it as a statement of its own"
            )


def _referenced_tables(tree: exp.Expression) -> list[str]:
    names: list[str] = []
    for table in tree.find_all(exp.Table):
        parts = [p for p in (table.db, table.name) if p]
        if parts:
            names.append(".".join(str(p) for p in parts).lower())
    return sorted(set(names))


def _check_raw_text(statement: str) -> None:
    """Run *before* parsing, for two reasons: the refusal reason stays
    accurate (``INTO OUTFILE`` is not valid sqlglot input, so parsing it
    first would report "could not parse" and hide what was actually
    attempted), and hostile text never has to survive a parser to be
    rejected."""
    for pattern, why in BANNED_PATTERNS:
        if pattern.search(statement):
            raise SqlBlocked(why)


def _check_constructs(tree: exp.Expression) -> None:
    for func in tree.find_all(exp.Anonymous):
        name = str(func.this or "").lower()
        if name in BANNED_FUNCTIONS or name.startswith(BANNED_FUNCTION_PREFIXES):
            raise SqlBlocked(f"function {name}() is not allowed")
    # sqlglot models some of these as typed nodes rather than Anonymous.
    for node in tree.walk():
        key = getattr(node, "key", "")
        if isinstance(key, str) and key.lower() in BANNED_FUNCTIONS:
            raise SqlBlocked(f"function {key.lower()}() is not allowed")


def _is_system_table(full: str, engine: str) -> bool:
    """Does this name *resolve to* a system object on this engine?

    Resolution, not spelling, is what matters. Postgres searches `pg_catalog`
    implicitly and first, so an unqualified `pg_roles` is
    `pg_catalog.pg_roles`; the old check only caught the qualified spelling.
    A qualified `public.pg_notes` cannot resolve into the catalog, so it
    stays legal. MySQL resolves unqualified names against the connected
    database only, so the prefix rule does not apply there.
    """
    parts = full.split(".")
    bare = parts[-1]
    schema = parts[-2] if len(parts) > 1 else ""
    if schema in BANNED_SCHEMAS or bare in BANNED_SCHEMAS:
        return True
    in_pg_catalog = schema.startswith("pg_") or (not schema and bare.startswith("pg_"))
    if engine == "postgres" and in_pg_catalog:
        return True
    # sqlite_master, sqlite_schema (its modern alias), sqlite_temp_*,
    # sqlite_sequence, sqlite_stat*: every internal table shares the prefix.
    return engine == "sqlite" and bare.startswith("sqlite_")


def _check_tables(tables: list[str], perms: Permissions, engine: str) -> None:
    for full in tables:
        bare = full.split(".")[-1]
        if _is_system_table(full, engine):
            raise SqlBlocked(f"{full} is a system catalog and cannot be queried directly")
        if perms.deny_tables and (bare in perms.deny_tables or full in perms.deny_tables):
            raise SqlBlocked(f"table {full} is denied for this connection")
        # An allow-list, when present, is exhaustive.
        if perms.allow_tables and not (bare in perms.allow_tables or full in perms.allow_tables):
            raise SqlBlocked(f"table {full} is not in this connection's allowed tables")


def _apply_limit(tree: exp.Expression, row_limit: int) -> int | None:
    """Force a LIMIT onto a top-level read, tightening an existing one that is
    too large. Returns the limit now in force, or None if not applicable.

    Every read shape, not just a bare SELECT: this used to early-return for
    anything that was not `exp.Select`, so every UNION/EXCEPT/INTERSECT ran
    unbounded. The limit goes on the *outer* node — a LIMIT inside one branch
    of a UNION caps that branch only.

    The `exp.With` unwrap is the same version-defensive measure as in
    `_classify`."""
    target = tree.this if isinstance(tree, exp.With) else tree
    if not isinstance(target, READ_TYPES):
        return None

    existing = target.args.get("limit")
    if existing is not None:
        try:
            current = int(existing.expression.this)
        except (AttributeError, TypeError, ValueError):
            current = row_limit + 1  # unparseable (FETCH, LIMIT ALL, a subquery) → too large
        # SQLite reads `LIMIT -1` as *no limit*, so a negative is never
        # "already within the cap".
        if 0 <= current <= row_limit:
            return current
    target.set("limit", exp.Limit(expression=exp.Literal.number(row_limit)))
    return row_limit


def guard(statement: str, *, engine: str, permissions: Permissions) -> GuardResult:
    """Validate, classify and rewrite one statement. Raises `SqlBlocked`."""
    text = (statement or "").strip().rstrip(";").strip()
    if not text:
        raise SqlBlocked("empty statement")

    dialect = DIALECTS.get(engine)
    if dialect is None:
        raise SqlBlocked(f"{engine} does not accept SQL statements")

    _check_raw_text(text)

    try:
        trees = sqlglot.parse(text, dialect=dialect)
    except Exception as exc:
        raise SqlBlocked(f"could not parse the statement: {exc}") from exc

    # sqlglot types its result as `list[Expr | None]`, where `Expr` is its own
    # alias rather than `exp.Expression`; a None entry is a trailing separator,
    # not a statement.
    parsed = [cast(exp.Expression, t) for t in trees if t is not None]
    if len(parsed) != 1:
        # Stacked statements are the classic way to smuggle a write behind a
        # read. One statement per call, always.
        raise SqlBlocked("exactly one statement per query is allowed")
    tree = parsed[0]

    kind = _classify(tree)
    _check_nested_mutations(tree)
    if kind == "read" and not permissions.read:
        raise SqlBlocked("this connection cannot read")
    if kind == "write" and not permissions.write:
        raise SqlBlocked("this connection is read-only; writes are not permitted")
    if kind == "ddl" and not permissions.ddl:
        raise SqlBlocked("this connection cannot run schema changes")

    _check_constructs(tree)
    tables = _referenced_tables(tree)
    _check_tables(tables, permissions, engine)

    limit = _apply_limit(tree, permissions.row_limit) if kind == "read" else None
    rendered = tree.sql(dialect=dialect)

    return GuardResult(
        statement=rendered,
        kind=kind,
        tables=tables,
        limit_applied=limit,
        mutating=kind in ("write", "ddl"),
        returns_rows=bool(tree.args.get("returning")),
    )


__all__ = [
    "BANNED_FUNCTIONS",
    "BANNED_PATTERNS",
    "BANNED_SCHEMAS",
    "DIALECTS",
    "GuardResult",
    "Permissions",
    "SqlBlocked",
    "guard",
]
