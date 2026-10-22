"""A missing service fails a run that requires it (Phase 7b.9).

Without this, CI with a broken Postgres container would skip every
integration test and still be green.
"""

from __future__ import annotations

import pytest
from tests.infra import SERVICES, required, unavailable


def test_without_the_setting_a_missing_service_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REQUIRE_INTEGRATION", raising=False)
    assert isinstance(_outcome("postgres"), pytest.skip.Exception)


def _outcome(service: str) -> BaseException:
    """What `unavailable` raised. Caught as BaseException: pytest's skip and
    fail are outcomes, and a skip slips past `pytest.raises(fail)` and
    skips the test instead of failing it."""
    try:
        unavailable(service, f"no {service}")
    except BaseException as exc:
        return exc
    raise AssertionError("unavailable() must not return")


def test_a_required_service_fails_and_another_still_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REQUIRE_INTEGRATION", "postgres, Redis")
    assert required() == {"postgres", "redis"}
    failed = _outcome("postgres")
    assert isinstance(failed, pytest.fail.Exception), type(failed)
    assert "REQUIRE_INTEGRATION includes postgres" in str(failed)
    assert isinstance(_outcome("redis"), pytest.fail.Exception)
    assert isinstance(_outcome("models"), pytest.skip.Exception)


def test_all_means_every_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUIRE_INTEGRATION", "all")
    assert required() == SERVICES


def test_a_misspelt_service_is_a_bug_in_the_test() -> None:
    with pytest.raises(AssertionError):
        unavailable("postgresql", "typo")
