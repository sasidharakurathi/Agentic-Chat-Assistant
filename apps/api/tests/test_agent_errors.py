"""Typed agent failures (task 5.4): every way a turn can fail maps to a
stable code, a message safe to show anyone, and whether retrying may help.
"""

from __future__ import annotations

import pytest
from app.agent.errors import PROCESS_LEVEL, classify, failure, from_message_error
from claude_agent_sdk import (
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    ResultError,
)


def _result(status: int | None = None, text: str = "", subtype: str = "success") -> ResultError:
    return ResultError(
        "run failed",
        data={
            "subtype": subtype,
            "api_error_status": status,
            "result": text,
            "terminal_reason": "api_error",
        },
        exit_code=1,
    )


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (CLINotFoundError(), "agent_not_installed", False),
        (CLIConnectionError("no"), "agent_unavailable", True),
        (ProcessError("died", exit_code=1, stderr="Traceback: secret"), "agent_crashed", True),
        (CLIJSONDecodeError("{bad", ValueError("x")), "protocol_error", True),
        (TimeoutError(), "timeout", True),
        (RuntimeError("anything else"), "agent_error", True),
        (_result(429), "rate_limited", True),
        (_result(529), "overloaded", True),
        (_result(503), "overloaded", True),
        (_result(500), "overloaded", True),
        (_result(401), "auth_failed", False),
        (_result(403), "auth_failed", False),
        (_result(402), "billing", False),
        (_result(400, "API Error: prompt is too long: 250000 tokens"), "context_too_long", False),
        (_result(400, "bad field"), "invalid_request", False),
        (_result(None, "API Error: Request timed out"), "timeout", True),
        (_result(None, "Overloaded"), "overloaded", True),
        (_result(subtype="error_max_turns"), "max_turns", False),
        (_result(subtype="error_max_budget_usd"), "budget_exceeded", False),
    ],
)
def test_each_failure_has_a_code(exc: BaseException, code: str, retryable: bool) -> None:
    got = classify(exc)
    assert (got.code, got.retryable) == (code, retryable)


def test_the_message_never_carries_the_exception_text() -> None:
    """The old driver sent `str(exc)`, stderr included, to the browser."""
    got = classify(ProcessError("died", exit_code=1, stderr="sk-ant-api03-SECRET at /home/app"))
    assert "SECRET" not in got.message and "/home" not in got.message
    assert got.message == "The assistant stopped unexpectedly. Try again."


@pytest.mark.parametrize(
    ("label", "code"),
    [
        ("authentication_failed", "auth_failed"),
        ("billing_error", "billing"),
        ("rate_limit", "rate_limited"),
        ("invalid_request", "invalid_request"),
        ("server_error", "overloaded"),
        ("unknown", "agent_error"),
        ("something-new", "agent_error"),
    ],
)
def test_the_clis_own_error_labels(label: str, code: str) -> None:
    assert from_message_error(label).code == code


def test_only_process_failures_are_retried_by_the_platform() -> None:
    """API trouble has already been retried inside the CLI."""
    assert {"agent_unavailable", "agent_crashed", "protocol_error"} == PROCESS_LEVEL
    assert failure("no-such-code").code == "agent_error"
