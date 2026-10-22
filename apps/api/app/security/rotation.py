"""KEK rotation across every stored secret (task 3.1).

`crypto.rewrap` has existed since Phase 3 and nothing called it, so there was
no way to actually rotate `APP_KEK`. This walks the `secrets` table and
re-wraps each row's data key under the new KEK. Only the 32-byte DEK changes
(the envelope's whole point), so a rotation is cheap however many secrets
there are.

Properties that matter when this runs against production:

- **All or nothing.** Everything happens in one transaction, and each rewrap
  is proven by decrypting with the new KEK *before* the commit. A failure
  part-way (a bad old key, a corrupt row) leaves every secret exactly as it
  was, never a mix of old- and new-key rows.
- **Safe to re-run.** A row that already opens with the new KEK is counted
  and skipped, so an interrupted rotation followed by a retry does not fail
  on the rows it had already done.
- **No plaintext leaves this function**, and neither key is logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.second_factor import SEALED_KIND, UserSecondFactor
from app.models.secret import Secret
from app.security.crypto import CryptoError, SealedSecret, open_sealed, rewrap


@dataclass
class RotationReport:
    rotated: int = 0
    already_current: int = 0


class RotationError(RuntimeError):
    pass


Sealed = Secret | UserSecondFactor


def _kind(row: Sealed) -> str:
    # Authenticator secrets (Phase 8.4) are sealed under one kind of their own.
    return row.kind.value if isinstance(row, Secret) else SEALED_KIND


def _sealed(row: Sealed) -> SealedSecret:
    return SealedSecret(ciphertext=row.ciphertext, dek_wrapped=row.dek_wrapped, nonce=row.nonce)


def _opens_with(row: Sealed, kek: str) -> bool:
    try:
        open_sealed(_sealed(row), kind=_kind(row), kek=kek)
    except CryptoError:
        return False
    return True


async def rotate_kek(
    session: AsyncSession, *, old_kek: str, new_kek: str, dry_run: bool = False
) -> RotationReport:
    if old_kek == new_kek:
        raise RotationError("the new KEK is the same as the old one")
    report = RotationReport()
    now = datetime.now(UTC)
    rows: list[Sealed] = [
        *(await session.scalars(select(Secret).order_by(Secret.created_at))).all(),
        *(
            await session.scalars(select(UserSecondFactor).order_by(UserSecondFactor.created_at))
        ).all(),
    ]
    for row in rows:
        # Captured up front: a rollback expires every loaded object, and
        # reading `row.id` afterwards would be a lazy load outside the
        # async context (it crashed the abort path with MissingGreenlet).
        row_id = row.id
        if _opens_with(row, new_kek):
            report.already_current += 1
            continue
        try:
            fresh = rewrap(_sealed(row), kind=_kind(row), old_kek=old_kek, new_kek=new_kek)
        except CryptoError as exc:
            await session.rollback()
            raise RotationError(
                f"secret {row_id} opens with neither key; nothing was changed"
            ) from exc
        row.dek_wrapped = fresh.dek_wrapped
        row.rotated_at = now
        if not _opens_with(row, new_kek):  # pragma: no cover - rewrap is deterministic
            await session.rollback()
            raise RotationError(f"secret {row_id} failed verification; nothing was changed")
        report.rotated += 1
    report.rotated += await _rotate_requests(session, old_kek=old_kek, new_kek=new_kek)
    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    return report


async def _rotate_requests(session: AsyncSession, *, old_kek: str, new_kek: str) -> int:
    """Requests still waiting carry their exact input sealed (Phase 9.1)."""
    from app.models.approval import SEALED_KIND as REQUEST_KIND
    from app.models.approval import Approval

    rotated = 0
    rows = (
        await session.scalars(select(Approval).where(Approval.sealed_ciphertext.is_not(None)))
    ).all()
    for row in rows:
        row_id = row.id
        assert row.sealed_ciphertext and row.sealed_dek and row.sealed_nonce
        box = SealedSecret(
            ciphertext=row.sealed_ciphertext, dek_wrapped=row.sealed_dek, nonce=row.sealed_nonce
        )
        try:
            open_sealed(box, kind=REQUEST_KIND, kek=new_kek)
            continue
        except CryptoError:
            pass
        try:
            fresh = rewrap(box, kind=REQUEST_KIND, old_kek=old_kek, new_kek=new_kek)
        except CryptoError as exc:
            await session.rollback()
            raise RotationError(
                f"request {row_id} opens with neither key; nothing was changed"
            ) from exc
        row.sealed_dek = fresh.dek_wrapped
        rotated += 1
    return rotated


__all__ = ["RotationError", "RotationReport", "rotate_kek"]
