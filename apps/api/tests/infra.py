"""What an integration test does when its service isn't there (Phase 7b.9).

On a laptop without Docker up, it skips: the unit tier still runs. In CI,
and in `scripts/check.ps1`, a skip would read as a pass, so
`REQUIRE_INTEGRATION` names the services that must be there, and a test
that can't reach one of them fails instead:

    REQUIRE_INTEGRATION=postgres,redis,mysql,mongodb,models

`all` means every one. The names are the first argument of `unavailable`.
"""

from __future__ import annotations

import os
from typing import NoReturn

import pytest

#: The services an integration test may need, as `unavailable` names them.
SERVICES = frozenset({"postgres", "redis", "mysql", "mongodb", "models"})


def required() -> frozenset[str]:
    raw = os.environ.get("REQUIRE_INTEGRATION", "")
    names = {n.strip().lower() for n in raw.split(",") if n.strip()}
    return SERVICES if "all" in names else frozenset(names)


def unavailable(service: str, why: str) -> NoReturn:
    """Skip, or fail when this run requires `service`."""
    assert service in SERVICES, f"unknown service {service!r}"
    if service in required():
        pytest.fail(f"{why} (REQUIRE_INTEGRATION includes {service})", pytrace=False)
    pytest.skip(why)
