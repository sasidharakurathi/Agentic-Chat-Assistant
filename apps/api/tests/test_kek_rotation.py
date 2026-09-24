"""Rotating APP_KEK actually works (task 3.1).

`rewrap()` existed and nothing called it; `rotated_at` was never written. Now
`rotate_kek` re-wraps every secret in one transaction, proves each against the
new key before committing, and is safe to re-run.
"""

from __future__ import annotations

import base64
import os
import uuid

import pytest
from app.config import settings
from app.db.session import get_sessionmaker
from app.models.integration import DbConnection
from app.models.secret import Secret
from app.security.crypto import CryptoError, SealedSecret, open_sealed
from app.security.rotation import RotationError, rotate_kek
from app.services import db_connections as db_svc
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.anyio


def _new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


async def _connection_with_password(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    aid = (await client.post("/api/v1/assistants", json={"name": "K"}, headers=headers)).json()[
        "id"
    ]
    r = await client.post(
        f"/api/v1/assistants/{aid}/db-connections",
        json={
            "name": "PG",
            "engine": "postgres",
            "host": "db",
            "database": "x",
            "username": "u",
            "password": "correct-horse-battery",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


def _opens(row: Secret, kek: str) -> bool:
    try:
        open_sealed(
            SealedSecret(ciphertext=row.ciphertext, dek_wrapped=row.dek_wrapped, nonce=row.nonce),
            kind=row.kind.value,
            kek=kek,
        )
    except CryptoError:
        return False
    return True


async def _secrets() -> list[Secret]:
    async with get_sessionmaker()() as s:
        return list((await s.scalars(select(Secret))).all())


async def test_a_rotation_moves_every_secret_to_the_new_key(
    client: AsyncClient, org_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = await _connection_with_password(client, org_headers)
    old, new = settings.app_kek, _new_key()

    async with get_sessionmaker()() as s:
        report = await rotate_kek(s, old_kek=old, new_kek=new)
    assert report.rotated >= 1

    for row in await _secrets():
        assert _opens(row, new) and not _opens(row, old)
        assert row.rotated_at is not None

    # And the application reads credentials with the new key in place.
    monkeypatch.setattr(settings, "app_kek", new)
    async with get_sessionmaker()() as s:
        conn = await s.get(DbConnection, cid)
        assert conn is not None
        info = await db_svc.connection_info(s, conn)
    assert info.password == "correct-horse-battery"


async def test_a_dry_run_changes_nothing(client: AsyncClient, org_headers: dict[str, str]) -> None:
    await _connection_with_password(client, org_headers)
    before = {r.id: r.dek_wrapped for r in await _secrets()}
    async with get_sessionmaker()() as s:
        report = await rotate_kek(s, old_kek=settings.app_kek, new_kek=_new_key(), dry_run=True)
    assert report.rotated == len(before)
    assert {r.id: r.dek_wrapped for r in await _secrets()} == before


async def test_a_wrong_old_key_aborts_with_nothing_changed(
    client: AsyncClient, org_headers: dict[str, str]
) -> None:
    await _connection_with_password(client, org_headers)
    before = {r.id: r.dek_wrapped for r in await _secrets()}
    async with get_sessionmaker()() as s:
        with pytest.raises(RotationError):
            await rotate_kek(s, old_kek=_new_key(), new_kek=_new_key())
    assert {r.id: r.dek_wrapped for r in await _secrets()} == before


async def test_a_rerun_is_a_no_op(client: AsyncClient, org_headers: dict[str, str]) -> None:
    await _connection_with_password(client, org_headers)
    old, new = settings.app_kek, _new_key()
    async with get_sessionmaker()() as s:
        first = await rotate_kek(s, old_kek=old, new_kek=new)
    async with get_sessionmaker()() as s:
        again = await rotate_kek(s, old_kek=old, new_kek=new)
    assert again.rotated == 0 and again.already_current == first.rotated
