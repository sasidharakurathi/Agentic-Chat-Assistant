from __future__ import annotations

import re
import secrets

_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    base = _slug_re.sub("-", value.strip().lower()).strip("-")
    return base or "org"


def unique_slug(value: str, exists: object) -> str:
    """``exists`` is a callable ``(slug) -> bool``. Appends a short random suffix
    on collision."""
    base = slugify(value)[:100]
    candidate = base
    while exists(candidate):  # type: ignore[operator]
        candidate = f"{base}-{secrets.token_hex(3)}"
    return candidate


__all__ = ["slugify", "unique_slug"]
