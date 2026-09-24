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

from app.models.secret import Secret
from app.security.crypto import CryptoError, SealedSecret, open_sealed, rewrap


@dataclass
class RotationReport:
    rotated: int = 0
    already_current: int = 0


class RotationError(RuntimeError):
    pass


def _sealed(row: Secret) -> SealedSecret:
    return SealedSecret(ciphertext=row.ciphertext, dek_wrapped=row.dek_wrapped, nonce=row.nonce)


def _opens_with(row: Secret, kek: str) -> bool:
    try:
        open_sealed(_sealed(row), kind=row.kind.value, kek=kek)
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
    rows = (await session.scalars(select(Secret).order_by(Secret.created_at))).all()
    for row in rows:
        # Captured up front: a rollback expires every loaded object, and
        # reading `row.id` afterwards would be a lazy load outside the
        # async context (it crashed the abort path with MissingGreenlet).
        row_id = row.id
        if _opens_with(row, new_kek):
            report.already_current += 1
            continue
        try:
            fresh = rewrap(_sealed(row), kind=row.kind.value, old_kek=old_kek, new_kek=new_kek)
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
    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    return report


__all__ = ["RotationError", "RotationReport", "rotate_kek"]
