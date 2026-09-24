"""Envelope encryption for stored credentials (task 3.1).

Every secret gets its own random **data encryption key** (DEK). The plaintext
is sealed with the DEK; the DEK is then sealed with the process-wide **key
encryption key** (KEK, `APP_KEK`). Only the wrapped DEK and the ciphertext
reach the database.

Why the extra layer, when one key would "work":

* **Rotation is cheap.** Re-keying means unwrapping and re-wrapping a 32-byte
  DEK per row — never touching, decrypting or re-encrypting the payloads. A
  single-key scheme would have to rewrite every ciphertext, which is exactly
  the kind of migration nobody runs.
* **Blast radius.** A leaked DEK exposes one credential. The KEK lives only
  in the environment, never in the database, so a database dump on its own is
  inert.
* **Key separation.** The KEK can later move to a KMS/HSM that will wrap and
  unwrap but never hand back raw key material — that swap only touches
  `_wrap`/`_unwrap` here.

AES-256-GCM throughout: authenticated, so tampering with a stored ciphertext
fails loudly (`InvalidTag`) instead of silently decrypting to garbage.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

# 96-bit nonces are the size AES-GCM is specified for; anything else makes the
# implementation derive one, which is both slower and easier to get wrong.
NONCE_BYTES = 12
DEK_BYTES = 32

# Bound to the ciphertext by GCM: a value sealed as a db-connection password
# cannot be replayed into a context expecting some other secret kind, even by
# someone who can write to the table.
_AAD_PREFIX = b"assistant-studio/v1/"


class CryptoError(RuntimeError):
    """Decryption failed — wrong KEK, tampered ciphertext, or corrupt row."""


@dataclass(frozen=True)
class SealedSecret:
    """Exactly what gets persisted. No field here is sensitive on its own —
    the ciphertext needs the DEK, and the wrapped DEK needs the KEK."""

    ciphertext: bytes
    dek_wrapped: bytes
    nonce: bytes

    def __repr__(self) -> str:  # pragma: no cover - defensive
        # Never let a stray log line or traceback print key material.
        return (
            f"SealedSecret(ciphertext=<{len(self.ciphertext)}B>, "
            f"dek_wrapped=<{len(self.dek_wrapped)}B>, nonce=<{len(self.nonce)}B>)"
        )


def _kek(raw: str | None = None) -> AESGCM:
    value = raw if raw is not None else settings.app_kek
    if not value:
        raise CryptoError("APP_KEK is not configured; cannot seal or open secrets")
    try:
        key = base64.b64decode(value, validate=True)
    except Exception as exc:  # pragma: no cover - config validation covers shape
        raise CryptoError("APP_KEK is not valid base64") from exc
    if len(key) != DEK_BYTES:
        raise CryptoError("APP_KEK must decode to exactly 32 bytes")
    return AESGCM(key)


def _aad(kind: str) -> bytes:
    return _AAD_PREFIX + kind.encode("utf-8")


def seal(plaintext: str, *, kind: str, kek: str | None = None) -> SealedSecret:
    """Encrypt `plaintext` under a fresh DEK, and wrap that DEK with the KEK.

    The same nonce is reused for both operations on purpose: it is unique per
    secret (freshly random here), and the two encryptions use *different*
    keys, so the nonce-reuse hazard AES-GCM has within a single key does not
    arise. Storing one nonce instead of two keeps the row honest about what
    it is.
    """
    nonce = os.urandom(NONCE_BYTES)
    dek = AESGCM.generate_key(bit_length=256)
    aad = _aad(kind)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), aad)
    dek_wrapped = _kek(kek).encrypt(nonce, dek, aad)
    return SealedSecret(ciphertext=ciphertext, dek_wrapped=dek_wrapped, nonce=nonce)


def open_sealed(sealed: SealedSecret, *, kind: str, kek: str | None = None) -> str:
    """Recover the plaintext. Raises `CryptoError` on any failure — a wrong
    KEK, a tampered ciphertext and a corrupt row are indistinguishable to the
    caller on purpose, since telling them apart is a decryption oracle."""
    aad = _aad(kind)
    try:
        dek = _kek(kek).decrypt(sealed.nonce, sealed.dek_wrapped, aad)
        return AESGCM(dek).decrypt(sealed.nonce, sealed.ciphertext, aad).decode("utf-8")
    except InvalidTag as exc:
        raise CryptoError("secret could not be decrypted") from exc


def rewrap(sealed: SealedSecret, *, kind: str, old_kek: str, new_kek: str) -> SealedSecret:
    """Rotate the KEK without touching the payload.

    This is the whole reason for the envelope: only the 32-byte DEK is
    re-encrypted, so rotating a key across a million secrets is a million
    tiny operations rather than a full re-encryption of every credential.
    """
    aad = _aad(kind)
    try:
        dek = _kek(old_kek).decrypt(sealed.nonce, sealed.dek_wrapped, aad)
    except InvalidTag as exc:
        raise CryptoError("secret could not be unwrapped with the old KEK") from exc
    return SealedSecret(
        ciphertext=sealed.ciphertext,
        dek_wrapped=_kek(new_kek).encrypt(sealed.nonce, dek, aad),
        nonce=sealed.nonce,
    )


def generate_kek() -> str:
    """A fresh base64 KEK (32 random bytes). `scripts/setup.ps1` inlines the
    same two lines rather than importing this, because it runs before a
    `.env` exists and importing the app would fail settings validation."""
    return base64.b64encode(os.urandom(DEK_BYTES)).decode("ascii")


__all__ = [
    "DEK_BYTES",
    "NONCE_BYTES",
    "CryptoError",
    "SealedSecret",
    "generate_kek",
    "open_sealed",
    "rewrap",
    "seal",
]
