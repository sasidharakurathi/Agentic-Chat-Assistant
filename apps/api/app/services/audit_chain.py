"""The audit log's hash chain (Phase 7b.7).

The append-only trigger (7a.8) stops the app from changing or deleting an
audit row. It doesn't stop someone with direct access to the database, who
could also drop the trigger. The chain makes such a change visible: each
row, once sealed, carries a sequence number and a hash over its own
contents and the previous row's hash. Altering, removing or reordering a
sealed row breaks every hash from there on, and `verify` says where.

What is hashed: the row's id, when, action, target, details, address and
the actor's email at the time. Not the two references a deletion clears by
itself (`org_id`, `actor_user_id`, both `ON DELETE SET NULL`): removing a
user must not look like tampering. The email in `actor_label` still says
who it was.

Sealing runs in one place at a time (the worker, every minute; an advisory
lock on Postgres keeps a second sealer out), in the order rows were written.
Rows waiting for the next run are listed as unsealed, not as broken.

This is evidence, not prevention: someone who can write to the database
can also rewrite the whole chain after the point they changed. Keep the
backups (which hold earlier chains) and, for stronger proof, the newest
`row_hash` copied somewhere they can't reach.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models.audit_log import AuditLog

log = get_logger(__name__)

#: The value before the first row.
GENESIS = "0" * 64
#: Rows sealed per run: the rest wait for the next.
BATCH = 2000
#: Postgres advisory lock key: one sealer at a time.
_LOCK = 7_207_207


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="microseconds")


def row_hash(row: AuditLog, prev: str, seq: int | None = None) -> str:
    body: dict[str, Any] = {
        "prev": prev,
        "seq": seq if seq is not None else row.seq,
        "id": str(row.id),
        "at": _iso(row.created_at),
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "meta": row.meta or {},
        "ip": row.ip,
        "actor": row.actor_label,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _locked(session: AsyncSession) -> bool:
    if session.bind.dialect.name != "postgresql":
        return True
    got = await session.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _LOCK})
    return bool(got)


async def seal(session: AsyncSession, *, batch: int = BATCH) -> int:
    """Seal the rows written since the last run, in the order they were
    written. Returns how many. Commits."""
    if not await _locked(session):
        return 0
    last = await session.execute(
        select(AuditLog.seq, AuditLog.row_hash)
        .where(AuditLog.seq.is_not(None))
        .order_by(AuditLog.seq.desc())
        .limit(1)
        .execution_options(all_orgs=True)
    )
    found = last.first()
    seq, prev = (found[0], found[1]) if found else (0, GENESIS)
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.seq.is_(None))
            .order_by(AuditLog.created_at, AuditLog.id)
            .limit(batch)
            .execution_options(all_orgs=True)
        )
    ).all()
    for row in rows:
        seq += 1
        digest = row_hash(row, prev, seq)
        await session.execute(
            update(AuditLog)
            .where(AuditLog.id == row.id, AuditLog.seq.is_(None))
            .values(seq=seq, prev_hash=prev, row_hash=digest)
            .execution_options(synchronize_session=False, all_orgs=True)
        )
        prev = digest
    await session.commit()
    if rows:
        log.info("audit_sealed", rows=len(rows), last_seq=seq)
    return len(rows)


@dataclass(frozen=True)
class Integrity:
    sealed: int
    unsealed: int
    #: Every sealed row's hash matches its contents and its predecessor.
    intact: bool
    #: The first row that doesn't, when one doesn't.
    broken_at: int | None
    last_hash: str | None


async def verify(session: AsyncSession) -> Integrity:
    """Walk the chain from the start and recompute every hash."""
    prev, expected_seq, sealed = GENESIS, 1, 0
    result = await session.stream_scalars(
        select(AuditLog)
        .where(AuditLog.seq.is_not(None))
        .order_by(AuditLog.seq)
        .execution_options(all_orgs=True, yield_per=1000)
    )
    broken: int | None = None
    async for row in result:
        sealed += 1
        if broken is None and (
            row.seq != expected_seq or row.prev_hash != prev or row.row_hash != row_hash(row, prev)
        ):
            broken = row.seq
        prev = row.row_hash or prev
        expected_seq = (row.seq or expected_seq) + 1
    unsealed = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.seq.is_(None))
        .execution_options(all_orgs=True)
    )
    return Integrity(
        sealed=sealed,
        unsealed=int(unsealed or 0),
        intact=broken is None,
        broken_at=broken,
        last_hash=prev if sealed else None,
    )


__all__ = ["GENESIS", "Integrity", "row_hash", "seal", "verify"]
