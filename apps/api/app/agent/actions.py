"""Declared actions (Phase 9.1, plan D7 and D8).

Until now the only way an assistant could change data was `sql_query`: the
model wrote the statement, a parser decided it was a write, and someone
approved it. That can't support "only the asker's own rows": the model
writes the WHERE clause, so nothing proves a statement touches only the
asker's data, and every write had to go to an admin.

A declared action is a statement the **builder** writes, on a database
node, with named slots:

    INSERT INTO access_requests (app, reason, requester_email)
    VALUES (:app, :reason, :asker_email)

- `:app` and `:reason` are **parameters** the model fills, each with a type
  (and, optionally, a list of allowed values), checked before anything runs.
- `:asker_email`, `:asker_name` and `:asker_id` are **filled by the
  platform** from the signed-in person, never from the model.
- Its **tier** says how it runs: `read` at once, `confirm` once the asker
  confirms, `approver` once one of the assistant's approvers decides. It is
  part of the version, so an admin approves it with everything else.

`confirm` is accepted only for a statement that is provably about the
asker: every inserted row carries an asker slot, or an UPDATE or DELETE has
`column = :asker_…` as one of the AND-ed conditions of its WHERE (an OR
around it would let the model reach other rows). That is checked when the
config is saved.

**Rendering.** Values are never pasted into the text. The template is
parsed, each slot becomes a literal node, and the statement is generated
for the connection's dialect; the result is parsed again and must hold
exactly the values given, or nothing runs. The SQL guard then checks the
rendered statement like any other.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

import sqlglot
from sqlglot import exp

from app.agent.caps import CapabilityTool, _err, _text
from app.datasources.sql_guard import DIALECTS, Permissions, SqlBlocked, guard
from app.logging import get_logger

if TYPE_CHECKING:
    from app.schemas.assistant_config import (
        ActionParam,
        AssistantConfig,
        DatabaseRef,
        DeclaredAction,
        HttpAction,
    )

    #: Either kind of declared action: they share a name, title, params, tier.
    AnyAction = DeclaredAction | HttpAction

log = get_logger(__name__)

#: Filled by the platform from the person asking; never a parameter.
BOUND_SLOTS = frozenset({"asker_email", "asker_name", "asker_id"})
#: Each action is the tool `act_<name>` on the platform's own server.
TOOL_PREFIX = "act_"

_SLOT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_INTEGER = re.compile(r"^-?\d{1,18}$")
_NUMBER = re.compile(r"^-?\d{1,18}(\.\d{1,18})?$")
#: Control characters a value may not carry (tabs and line breaks may).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: Classification only, as in the approval router: the most permissive
#: profile, so the only refusals are ones no permission could lift.
_PERMISSIVE = Permissions(read=True, write=True, ddl=True, row_limit=10_000)


class ActionInputError(ValueError):
    """The model's parameters don't fit the action. Said back to the model,
    which can fix them; nothing is stored or run."""


@dataclass(frozen=True)
class Asker:
    """Who is asking, as the platform knows them."""

    user_id: uuid.UUID
    email: str
    name: str

    def slots(self) -> dict[str, str]:
        return {
            "asker_email": self.email,
            "asker_name": self.name,
            "asker_id": str(self.user_id),
        }


def tool_name(action: AnyAction) -> str:
    return f"{TOOL_PREFIX}{action.name}"


def qualified_name(action: AnyAction) -> str:
    from app.agent.caps import CAPS_SERVER_NAME

    return f"mcp__{CAPS_SERVER_NAME}__{tool_name(action)}"


def find(config: AssistantConfig, qualified: str) -> tuple[DatabaseRef, DeclaredAction] | None:
    """The action (and its database) a qualified tool name stands for."""
    for ref in config.databases:
        for action in ref.actions:
            if qualified_name(action) == qualified:
                return ref, action
    return None


def tiers(config: AssistantConfig) -> dict[str, str]:
    """Qualified tool name → tier, for the approval router. SQL and HTTP
    actions (Phase 9.3) alike."""
    out: dict[str, str] = {qualified_name(a): a.tier for d in config.databases for a in d.actions}
    out.update({qualified_name(a): a.tier for a in config.tools.http_request.actions})
    return out


def any_action(config: AssistantConfig, qualified: str) -> AnyAction | None:
    """The SQL or HTTP action a qualified tool name stands for."""
    if (found := find(config, qualified)) is not None:
        return found[1]
    from app.agent.http_actions import find_http

    return find_http(config, qualified)


# ── checking a template (when the config is saved) ───────────


def _parse_any(sql: str) -> exp.Expr:
    """The template as one statement, in whichever dialect reads it."""
    first = ""
    for dialect in ("postgres", "mysql", "sqlite"):
        try:
            trees = [t for t in sqlglot.parse(sql, read=dialect) if t is not None]
        except sqlglot.errors.ParseError as exc:
            first = first or str(exc).splitlines()[0]
            continue
        if len(trees) != 1:
            raise ValueError("an action is exactly one statement")
        return trees[0]
    raise ValueError(f"the statement can't be read: {first}")


def _slot_names(tree: exp.Expr) -> list[str]:
    names = []
    for node in tree.find_all(exp.Placeholder):
        name = node.name or ""
        if not _SLOT_NAME.match(name):
            raise ValueError("use named slots such as :app, not ? or $1")
        names.append(name)
    return names


def _sample(param: ActionParam) -> Any:
    return {
        "string": param.choices[0] if param.choices else "x",
        "integer": 1,
        "number": 1.5,
        "boolean": True,
        "date": "2026-01-01",
    }[param.type]


def _conjuncts(node: exp.Expr) -> list[exp.Expr]:
    """The AND-ed conditions at the top of a WHERE clause."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.And):
        return _conjuncts(node.left) + _conjuncts(node.right)
    return [node]


#: The slots that prove who someone is. A name is chosen by the person
#: (and can copy a colleague's), so it never pins a write to them (9.9).
PINNING_SLOTS = frozenset({"asker_email", "asker_id"})


def _asker_condition(node: exp.Expr) -> bool:
    """`column = :asker_email` (or `:asker_id`), either way round."""
    if not isinstance(node, exp.EQ):
        return False
    sides = (node.left, node.right)
    return any(
        isinstance(a, exp.Placeholder) and a.name in PINNING_SLOTS and isinstance(b, exp.Column)
        for a, b in (sides, sides[::-1])
    )


def pinned_to_asker(tree: exp.Expr) -> bool:
    """Whether a write provably touches only the asker's own rows."""
    if isinstance(tree, exp.Insert):
        if tree.args.get("conflict") is not None:
            # ON CONFLICT … DO UPDATE / ON DUPLICATE KEY UPDATE changes
            # whichever row it collides with, which may be someone else's.
            return False
        values = tree.expression
        if not isinstance(values, exp.Values) or not values.expressions:
            return False
        return all(
            any(isinstance(e, exp.Placeholder) and e.name in PINNING_SLOTS for e in row.expressions)
            for row in values.expressions
        )
    if isinstance(tree, (exp.Update, exp.Delete)):
        where = tree.args.get("where")
        return where is not None and any(_asker_condition(c) for c in _conjuncts(where.this))
    return False


def check_template(action: DeclaredAction) -> None:
    """Refuse an action that can't be run safely, with a reason the builder
    can act on. Raises ValueError (a validation error on save)."""
    where = f"action {action.name!r}"
    try:
        tree = _parse_any(action.sql)
        used = set(_slot_names(tree))
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from exc
    declared = {p.name for p in action.params}
    if unknown := sorted(used - declared - BOUND_SLOTS):
        raise ValueError(f"{where}: no parameter named {', '.join(':' + n for n in unknown)}")
    if unused := sorted(declared - used):
        raise ValueError(f"{where}: the statement never uses {', '.join(unused)}")

    values: dict[str, Any] = {p.name: _sample(p) for p in action.params}
    values.update(dict.fromkeys(BOUND_SLOTS, "x"))
    kind = None
    reason = ""
    for engine in ("postgres", "mysql", "sqlite"):
        try:
            rendered = _render_tree(action.sql, values, engine)
            kind = guard(rendered, engine=engine, permissions=_PERMISSIVE).kind
            break
        except (SqlBlocked, ActionInputError, sqlglot.errors.ParseError) as exc:
            reason = reason or str(exc)
    if kind is None:
        raise ValueError(f"{where}: the SQL guard refuses it: {reason}")
    if kind == "ddl":
        raise ValueError(f"{where}: actions can't change the database's structure")
    if kind == "read" and action.tier != "read":
        raise ValueError(f"{where}: it only reads, so its tier is 'read'")
    if kind == "write" and action.tier == "read":
        raise ValueError(f"{where}: it changes data, so it needs 'confirm' or 'approver'")
    if action.tier == "confirm" and not pinned_to_asker(tree):
        raise ValueError(
            f"{where}: 'confirm' is only for the asker's own rows: every inserted row "
            "needs an :asker_ value, or the WHERE clause needs `column = :asker_email` "
            "(or :asker_id) joined with AND. Otherwise use 'approver'."
        )


# ── rendering (when it is asked for) ─────────────────────────


def _coerce(param: ActionParam, raw: Any) -> Any:  # noqa: PLR0911, PLR0912 - one branch per type
    label = f"{param.name}"
    if raw is None or raw == "":
        if param.required:
            raise ActionInputError(f"{label} is required")
        return None
    if param.type == "string":
        if not isinstance(raw, str):
            raise ActionInputError(f"{label} must be text")
        if len(raw) > param.max_length:
            raise ActionInputError(f"{label} is longer than {param.max_length} characters")
        if _CONTROL.search(raw):
            raise ActionInputError(f"{label} contains control characters")
        if param.choices and raw not in param.choices:
            raise ActionInputError(f"{label} must be one of: {', '.join(param.choices)}")
        return raw
    if param.type == "integer":
        if isinstance(raw, bool):
            raise ActionInputError(f"{label} must be a whole number")
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, str) and _INTEGER.match(raw.strip()):
            value = int(raw.strip())
        else:
            raise ActionInputError(f"{label} must be a whole number")
        if abs(value) >= 2**63:
            raise ActionInputError(f"{label} is out of range")
        return value
    if param.type == "number":
        if isinstance(raw, bool):
            raise ActionInputError(f"{label} must be a number")
        if isinstance(raw, (int, float)):
            number = float(raw)
        elif isinstance(raw, str) and _NUMBER.match(raw.strip()):
            number = float(raw.strip())
        else:
            raise ActionInputError(f"{label} must be a number")
        if not math.isfinite(number):
            raise ActionInputError(f"{label} must be a number")
        return number
    if param.type == "boolean":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.lower() in ("true", "false"):
            return raw.lower() == "true"
        raise ActionInputError(f"{label} must be true or false")
    # date
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw.strip()).isoformat()
        except ValueError:
            pass
    raise ActionInputError(f"{label} must be a date like 2026-04-01")


def values_for(action: AnyAction, args: dict[str, Any]) -> dict[str, Any]:
    """The parameters, checked and typed. Raises ActionInputError."""
    if not isinstance(args, dict):
        raise ActionInputError("the parameters must be an object")
    known = {p.name for p in action.params}
    if extra := sorted(set(args) - known):
        raise ActionInputError(f"unknown parameter: {', '.join(extra)}")
    return {p.name: _coerce(p, args.get(p.name)) for p in action.params}


def _literal(value: Any) -> exp.Expr:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, (int, float)):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


def _strings(tree: exp.Expr) -> Counter[str]:
    return Counter(lit.this for lit in tree.find_all(exp.Literal) if lit.is_string)


def _render_tree(sql: str, values: dict[str, Any], engine: str) -> str:
    dialect = DIALECTS.get(engine)
    if dialect is None:
        raise ActionInputError(f"{engine} does not take SQL actions")
    tree = sqlglot.parse_one(sql, read=dialect)
    expected = _strings(tree)
    for node in tree.find_all(exp.Placeholder):
        value = values[node.name]
        if isinstance(value, str):
            expected[value] += 1

    def swap(node: exp.Expr) -> exp.Expr:
        if isinstance(node, exp.Placeholder):
            return _literal(values[node.name])
        return node

    rendered = tree.transform(swap).sql(dialect=dialect)
    # Read back: the same kind of statement, no slot left, and exactly the
    # text values given. Anything else means a value changed the statement.
    back = sqlglot.parse_one(rendered, read=dialect)
    if (
        type(back) is not type(tree)
        or any(True for _ in back.find_all(exp.Placeholder))
        or _strings(back) != expected
    ):
        raise ActionInputError("the values couldn't be put into the statement safely")
    return rendered


def render(
    action: DeclaredAction, args: dict[str, Any], *, asker: Asker, engine: str
) -> tuple[str, dict[str, Any]]:
    """The statement that would run, and the checked parameters."""
    values = values_for(action, args)
    return _render_tree(action.sql, {**values, **asker.slots()}, engine), values


def describe(action: AnyAction, values: dict[str, Any]) -> str:
    """The request in words: the title, then each value given."""
    lines = [action.title]
    for p in action.params:
        value = values.get(p.name)
        if value is not None:
            lines.append(f"{p.description or p.name}: {value}")
    return "\n".join(lines)


def input_schema(action: AnyAction) -> dict[str, Any]:
    kinds = {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "date": "string",
    }
    props: dict[str, Any] = {}
    for p in action.params:
        prop: dict[str, Any] = {"type": kinds[p.type]}
        if p.description:
            prop["description"] = p.description
        if p.type == "string":
            prop["maxLength"] = p.max_length
        if p.type == "date":
            prop["format"] = "date"
        if p.choices:
            prop["enum"] = list(p.choices)
        props[p.name] = prop
    return {
        "type": "object",
        "properties": props,
        "required": [p.name for p in action.params if p.required],
        "additionalProperties": False,
    }


# ── running ──────────────────────────────────────────────────


async def run_statement(  # noqa: PLR0911 - one return per refusal
    assistant_id: uuid.UUID, connection_id: str, statement: str, asker_email: str | None = None
) -> dict[str, Any]:
    """Run a rendered statement under the connection's own permissions.

    `expose_write` is not consulted: it is about SQL the model writes, and
    this statement was written by the builder and approved with the
    version. The credential must still allow it, and the guard still
    checks it (tables, functions, the row limit)."""
    from app.agent.caps_sql import _render, _writable
    from app.datasources import DbError, get_sql_adapter
    from app.db.session import get_sessionmaker
    from app.models.integration import DbEngine
    from app.services import db_connections as db_svc

    try:
        cid = uuid.UUID(connection_id)
    except ValueError:
        return _err("error: this action's database is not set up")
    try:
        async with get_sessionmaker()() as session:
            conn = await db_svc.get(session, assistant_id=assistant_id, connection_id=cid)
            info = await db_svc.connection_info(session, conn)
    except Exception as exc:
        return _err(f"error: {exc}")
    if conn.engine == DbEngine.mongodb:
        return _err("error: actions run on SQL databases only")
    perms = Permissions.from_dict(conn.permissions)
    if perms.scope_to_asker and not asker_email:
        return _err("error: this database answers only for a signed-in person")
    try:
        guarded = guard(statement, engine=conn.engine.value, permissions=perms)
    except SqlBlocked as exc:
        log.info("action_blocked", connection=str(conn.id), reason=str(exc))
        return _err(f"blocked: {exc}")
    if guarded.mutating and conn.engine == DbEngine.sqlite:
        info = _writable(info)
    adapter = get_sql_adapter(conn.engine.value)
    try:
        result = await adapter.run(
            info,
            guarded.statement,
            timeout_ms=perms.statement_timeout_ms,
            max_rows=perms.row_limit,
            mutating=guarded.mutating,
            returns_rows=guarded.returns_rows,
            asker_email=asker_email if perms.scope_to_asker else None,
        )
    except DbError as exc:
        return _err(f"error: {exc}")
    return _text(_render(result, guarded))


async def engine_of(assistant_id: uuid.UUID, connection_id: str) -> tuple[str, bool]:
    """The connection's engine and whether its credential may write."""
    from app.db.session import get_sessionmaker
    from app.services import db_connections as db_svc

    async with get_sessionmaker()() as session:
        conn = await db_svc.get(
            session, assistant_id=assistant_id, connection_id=uuid.UUID(connection_id)
        )
        return conn.engine.value, Permissions.from_dict(conn.permissions).write


def build_action_tools(
    assistant_id: uuid.UUID, databases: list[DatabaseRef], asker: Asker | None
) -> list[CapabilityTool]:
    """One tool per declared action. Only a `read` action's handler runs in
    a turn: the router stores the others as requests, and the executor runs
    the rendered statement once someone has decided (`run_statement`)."""
    tools = []
    for ref in databases:
        for action in ref.actions:
            tools.append(
                CapabilityTool(
                    name=tool_name(action),
                    description=_description(action),
                    input_schema=input_schema(action),
                    handler=_reader(assistant_id, ref.connection_id, action, asker),
                    read_only=action.tier == "read",
                )
            )
    return tools


def _description(action: DeclaredAction) -> str:
    text = action.title + (f". {action.description}" if action.description else "")
    if action.tier == "confirm":
        text += " The person asking confirms it on a card before it runs."
    elif action.tier == "approver":
        text += " It waits for an approver before it runs."
    return text


def _reader(
    assistant_id: uuid.UUID, connection_id: str, action: DeclaredAction, asker: Asker | None
) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        if action.tier != "read":
            # Never reached through the router, which stores it instead.
            return _err("error: this action runs only after it is decided")
        if asker is None:
            return _err("error: this action needs a signed-in person")
        try:
            engine, _ = await engine_of(assistant_id, connection_id)
            statement, _values = render(action, args, asker=asker, engine=engine)
        except ActionInputError as exc:
            return _err(f"error: {exc}")
        except Exception as exc:
            return _err(f"error: {exc}")
        return await run_statement(assistant_id, connection_id, statement, asker.email)

    return handler


def canonical(payload: dict[str, Any]) -> str:
    """The exact form that is sealed and hashed (sorted keys, no spaces)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "BOUND_SLOTS",
    "TOOL_PREFIX",
    "ActionInputError",
    "Asker",
    "any_action",
    "build_action_tools",
    "canonical",
    "check_template",
    "describe",
    "engine_of",
    "find",
    "input_schema",
    "pinned_to_asker",
    "qualified_name",
    "render",
    "run_statement",
    "tiers",
    "tool_name",
    "values_for",
]
