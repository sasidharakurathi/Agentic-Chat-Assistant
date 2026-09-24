"""Envelope encryption (task 3.1).

This is the module that stands between a database dump and every stored
credential, so the tests are about what an attacker gets rather than about
the happy path: a tampered row must fail loudly, the wrong KEK must fail,
plaintext must not appear anywhere in what gets persisted, and a secret
sealed for one purpose must not open in another.
"""

from __future__ import annotations

import base64
import os

import pytest
from app.security.crypto import (
    CryptoError,
    SealedSecret,
    generate_kek,
    open_sealed,
    rewrap,
    seal,
)

KEK_A = base64.b64encode(os.urandom(32)).decode()
KEK_B = base64.b64encode(os.urandom(32)).decode()

SECRET = "hunter2-correct-horse-battery-staple"


def test_round_trip() -> None:
    sealed = seal(SECRET, kind="db_password", kek=KEK_A)
    assert open_sealed(sealed, kind="db_password", kek=KEK_A) == SECRET


def test_nothing_persisted_contains_the_plaintext() -> None:
    """The actual property that matters for a stolen database."""
    sealed = seal(SECRET, kind="db_password", kek=KEK_A)
    blob = sealed.ciphertext + sealed.dek_wrapped + sealed.nonce
    assert SECRET.encode() not in blob
    # ...nor any recognisable fragment of it
    assert b"hunter2" not in blob


def test_each_seal_is_unique_even_for_identical_input() -> None:
    """Fresh DEK + fresh nonce per secret: two rows with the same password
    must not be visibly equal, or the table leaks which accounts share one."""
    a = seal(SECRET, kind="db_password", kek=KEK_A)
    b = seal(SECRET, kind="db_password", kek=KEK_A)
    assert a.ciphertext != b.ciphertext
    assert a.dek_wrapped != b.dek_wrapped
    assert a.nonce != b.nonce


def test_the_wrong_kek_cannot_open_it() -> None:
    sealed = seal(SECRET, kind="db_password", kek=KEK_A)
    with pytest.raises(CryptoError):
        open_sealed(sealed, kind="db_password", kek=KEK_B)


@pytest.mark.parametrize("field", ["ciphertext", "dek_wrapped", "nonce"])
def test_tampering_with_any_field_is_detected(field: str) -> None:
    """AES-GCM is authenticated: a modified row fails loudly instead of
    decrypting to plausible garbage."""
    sealed = seal(SECRET, kind="db_password", kek=KEK_A)
    raw = bytearray(getattr(sealed, field))
    raw[0] ^= 0xFF
    broken = SealedSecret(
        **{**sealed.__dict__, field: bytes(raw)}  # type: ignore[arg-type]
    )
    with pytest.raises(CryptoError):
        open_sealed(broken, kind="db_password", kek=KEK_A)


def test_a_secret_cannot_be_replayed_into_another_kind() -> None:
    """`kind` is bound as AES-GCM additional data, so someone who can write to
    the table cannot move a db password into, say, an api-key slot."""
    sealed = seal(SECRET, kind="db_password", kek=KEK_A)
    with pytest.raises(CryptoError):
        open_sealed(sealed, kind="api_key", kek=KEK_A)


# ── rotation ─────────────────────────────────────────────────


def test_rewrap_changes_the_key_without_touching_the_payload() -> None:
    """The whole point of the envelope: rotation re-encrypts 32 bytes, not
    the credential. The ciphertext must come out byte-identical."""
    sealed = seal(SECRET, kind="db_password", kek=KEK_A)
    rotated = rewrap(sealed, kind="db_password", old_kek=KEK_A, new_kek=KEK_B)

    assert rotated.ciphertext == sealed.ciphertext
    assert rotated.dek_wrapped != sealed.dek_wrapped
    assert open_sealed(rotated, kind="db_password", kek=KEK_B) == SECRET


def test_the_old_kek_stops_working_after_rotation() -> None:
    sealed = seal(SECRET, kind="db_password", kek=KEK_A)
    rotated = rewrap(sealed, kind="db_password", old_kek=KEK_A, new_kek=KEK_B)
    with pytest.raises(CryptoError):
        open_sealed(rotated, kind="db_password", kek=KEK_A)


def test_rewrap_with_the_wrong_old_kek_fails() -> None:
    sealed = seal(SECRET, kind="db_password", kek=KEK_A)
    with pytest.raises(CryptoError):
        rewrap(sealed, kind="db_password", old_kek=KEK_B, new_kek=KEK_A)


# ── configuration ────────────────────────────────────────────


def test_a_missing_kek_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(CryptoError, match="APP_KEK"):
        seal(SECRET, kind="db_password", kek="")


def test_a_wrong_length_kek_is_refused() -> None:
    short = base64.b64encode(os.urandom(16)).decode()
    with pytest.raises(CryptoError, match="32 bytes"):
        seal(SECRET, kind="db_password", kek=short)


def test_generate_kek_produces_a_usable_key() -> None:
    kek = generate_kek()
    assert len(base64.b64decode(kek)) == 32
    assert open_sealed(seal(SECRET, kind="api_key", kek=kek), kind="api_key", kek=kek) == SECRET


def test_repr_never_leaks_key_material() -> None:
    """A traceback or a stray log line must not print the ciphertext."""
    sealed = seal(SECRET, kind="db_password", kek=KEK_A)
    text = repr(sealed)
    assert "ciphertext=<" in text
    assert base64.b64encode(sealed.ciphertext).decode()[:16] not in text
    assert str(sealed.dek_wrapped) not in text


def test_unicode_and_empty_secrets_round_trip() -> None:
    for value in ["", "pa55 w0rd", "密码-şifre-пароль", "a" * 10_000]:
        sealed = seal(value, kind="api_key", kek=KEK_A)
        assert open_sealed(sealed, kind="api_key", kek=KEK_A) == value
