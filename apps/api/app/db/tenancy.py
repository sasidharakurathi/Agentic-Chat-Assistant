"""Structural org scoping for request sessions (plan §3, the repository
layer's job; task 0.7).

Tenant isolation was a convention: every query had to remember its own
`org_id` predicate, and nothing noticed one that didn't. Now, once a request
has established which org it acts in (the dependencies in `api/deps.py` bind
it after checking the caller's membership), every ORM SELECT, UPDATE and
DELETE that session runs gets `<table>.org_id = :bound_org` added to each
org-owned table it touches, via SQLAlchemy's `with_loader_criteria`. A
handler that forgets the filter gets nothing from another org rather than
its data.

Not covered, by design: sessions a request does not own (the chat stream's,
tool handlers', worker jobs'), which scope by assistant or source id, and
raw `text()` SQL. A query that must cross orgs inside a bound session says so
with `.execution_options(all_orgs=True)`.
"""

from __future__ import annotations

import uuid
from functools import cache
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.api.errors import NotFound
from app.db.base import Base

_KEY = "org_id"


def bind_org(session: Any, org_id: uuid.UUID) -> None:
    """Scope `session` (sync or async) to one org for the rest of its life.

    A request acts in one org. Binding a second, different one means two of
    its inputs disagree (say, an assistant from one org and an X-Org-Id for
    another), which is refused as not found rather than silently widened.
    """
    info = session.info
    bound = info.get(_KEY)
    if bound is not None and bound != org_id:
        raise NotFound("Not found")
    info[_KEY] = org_id


def bound_org(session: Any) -> uuid.UUID | None:
    value = session.info.get(_KEY)
    return value if isinstance(value, uuid.UUID) else None


def org_owned_models() -> tuple[type[Any], ...]:
    """Every mapped class with an `org_id` column. Keyed on the number of
    mappers, so a model imported after the first query is still covered."""
    return _org_owned(len(Base.registry.mappers))


@cache
def _org_owned(_mapper_count: int) -> tuple[type[Any], ...]:
    return tuple(
        sorted(
            (m.class_ for m in Base.registry.mappers if "org_id" in m.columns),
            key=lambda cls: cls.__name__,
        )
    )


@event.listens_for(Session, "do_orm_execute")
def _scope_to_bound_org(state: ORMExecuteState) -> None:
    org_id = state.session.info.get(_KEY)
    if org_id is None or state.execution_options.get("all_orgs"):
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    state.statement = state.statement.options(
        *(
            with_loader_criteria(model, lambda cls: cls.org_id == org_id, include_aliases=True)
            for model in org_owned_models()
        )
    )


__all__ = ["bind_org", "bound_org", "org_owned_models"]
