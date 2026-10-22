"""Time-based one-time codes (RFC 6238), as every authenticator app makes
them (Phase 8.4): HMAC-SHA1 over 30-second steps, 6 digits. Python's own
`hmac`, no package: the algorithm is a few lines and its test vectors are
in the RFC (tests/test_two_factor.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP_S = 30
DIGITS = 6
#: Codes one step either side of now also pass: a phone's clock drifts, and
#: a code typed in its last seconds arrives in the next step.
DRIFT_STEPS = 1


def new_secret() -> str:
    """160 random bits, base32, as authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at(secret: str | bytes, step: int, digits: int = DIGITS) -> str:
    key = secret if isinstance(secret, bytes) else _key(secret)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**digits).zfill(digits)


def step_of(at: float | None = None) -> int:
    return int((time.time() if at is None else at) // STEP_S)


def verify(secret: str, code: str, *, after_step: int = -1, at: float | None = None) -> int | None:
    """The step `code` belongs to, if it is right now (give or take one step)
    and later than `after_step`: a code used once can't be used again."""
    cleaned = "".join(ch for ch in code if ch.isdigit())
    if len(cleaned) != DIGITS:
        return None
    now = step_of(at)
    for step in range(now - DRIFT_STEPS, now + DRIFT_STEPS + 1):
        if step > after_step and hmac.compare_digest(code_at(secret, step), cleaned):
            return step
    return None


def uri(secret: str, *, account: str, issuer: str) -> str:
    """What the QR code holds: `otpauth://totp/Issuer:account?...`."""
    label = quote(f"{issuer}:{account}")
    query = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": DIGITS,
            "period": STEP_S,
        }
    )
    return f"otpauth://totp/{label}?{query}"


__all__ = ["DIGITS", "STEP_S", "code_at", "new_secret", "step_of", "uri", "verify"]
