"""What went wrong in a turn, typed (task 5.4).

The real driver used to catch everything and send the client
`agent_error` with `str(exc)`: the same code for a missing CLI, a rate limit
and a bad API key, and a message that could carry the CLI's stderr to the
browser. Now every failure is classified into:

- a stable **code** the UI and tests can branch on;
- a **message** that is safe to show anyone: no stack traces, no stderr, no
  request bodies (the full exception is logged server-side, with the turn's
  trace id);
- whether **trying again** may help, which the chat uses to offer a retry.

Two layers retry, and they don't overlap:

- **API-level** trouble (rate limits, overload, timeouts) is retried inside
  the CLI, which knows the request and backs off (`CLAUDE_CODE_MAX_RETRIES`,
  `API_TIMEOUT_MS`, set from settings). By the time one reaches the platform,
  those retries are spent; the user is told to try again shortly.
- **Process-level** trouble (the CLI didn't start, died, or spoke garbage)
  is something the CLI can't retry itself. The runtime restarts the turn
  once (`AGENT_TURN_RETRIES`), and only if nothing has streamed yet, so
  nothing is ever said or done twice (see `PROCESS_LEVEL`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Failure:
    code: str
    message: str
    retryable: bool


_KNOWN: dict[str, tuple[str, bool]] = {
    "agent_not_installed": (
        "The assistant's runtime isn't installed on this server. An administrator needs to "
        "fix this.",
        False,
    ),
    "agent_unavailable": ("The assistant's runtime couldn't be started. Try again.", True),
    "agent_crashed": ("The assistant stopped unexpectedly. Try again.", True),
    "protocol_error": ("The assistant's runtime sent something unreadable. Try again.", True),
    "rate_limited": ("The model is receiving too many requests. Try again in a minute.", True),
    "overloaded": ("The model is overloaded right now. Try again in a minute.", True),
    "timeout": ("The model took too long to respond. Try again.", True),
    "auth_failed": (
        "The platform's model credentials were rejected. An administrator needs to check them.",
        False,
    ),
    "billing": (
        "The model account can't be charged. An administrator needs to check its billing.",
        False,
    ),
    "context_too_long": (
        "This conversation is too long for the model. Start a new conversation, or lower the "
        "assistant's summary threshold (Panels > Memory).",
        False,
    ),
    "invalid_request": ("The model rejected the request.", False),
    # The API refuses a model the bundled CLI is too old for (Phase 7a.6:
    # "Claude Code 2.1.258 does not support this model; version 2.1.280 or
    # newer is required"). Trying again can't help.
    "model_unsupported": (
        "This server's assistant runtime is too old for the chosen model. An administrator "
        "needs to update the platform, or pick another model.",
        False,
    ),
    "agent_error": ("Something went wrong while answering. Try again.", True),
}

#: Failures the CLI could not have retried itself: the platform may restart
#: the turn, as long as nothing has streamed.
PROCESS_LEVEL = frozenset({"agent_unavailable", "agent_crashed", "protocol_error"})


def failure(code: str) -> Failure:
    message, retryable = _KNOWN.get(code, _KNOWN["agent_error"])
    return Failure(code if code in _KNOWN else "agent_error", message, retryable)


_BY_STATUS = {
    400: "invalid_request",
    401: "auth_failed",
    402: "billing",
    403: "auth_failed",
    408: "timeout",
    429: "rate_limited",
    503: "overloaded",
    504: "timeout",
    529: "overloaded",
}

#: In the API error's text, when its status says too little. First match wins;
#: "prompt is too long" comes back as a plain 400.
_BY_TEXT: tuple[tuple[tuple[str, ...], str], ...] = (
    (("prompt is too long", "context length"), "context_too_long"),
    (("timed out", "timeout"), "timeout"),
    (("overloaded",), "overloaded"),
    (("rate limit",), "rate_limited"),
)


def _api_failure(status: int | None, text: str) -> str:
    lowered = text.lower()
    for needles, code in _BY_TEXT[:1]:
        if any(n in lowered for n in needles):
            return code
    if status in _BY_STATUS:
        return _BY_STATUS[status]
    if status is not None and status >= 500:  # noqa: PLR2004 - HTTP server errors
        return "overloaded"
    for needles, code in _BY_TEXT[1:]:
        if any(n in lowered for n in needles):
            return code
    return "agent_error"


def _result_failure(exc: Any) -> Failure:
    """A run the CLI ended with an error result (`ResultError`)."""
    if exc.subtype == "error_max_turns":
        return Failure("max_turns", "The agent reached its turn limit for one message.", False)
    if exc.subtype == "error_max_budget_usd":
        return Failure("budget_exceeded", "This conversation has reached its spend limit.", False)
    return failure(_api_failure(exc.api_error_status, " ".join([exc.result or "", *exc.errors])))


def classify(exc: BaseException) -> Failure:
    """The typed failure for an exception from the agent runtime."""
    from claude_agent_sdk import (
        CLIConnectionError,
        CLIJSONDecodeError,
        CLINotFoundError,
        ProcessError,
        ResultError,
    )
    from claude_agent_sdk._errors import MessageParseError

    if isinstance(exc, ResultError):
        return _result_failure(exc)
    # Most specific first: a CLINotFoundError is also a CLIConnectionError,
    # and a ResultError (above) is also a ProcessError.
    kinds: tuple[tuple[type[BaseException] | tuple[type[BaseException], ...], str], ...] = (
        (CLINotFoundError, "agent_not_installed"),
        (CLIConnectionError, "agent_unavailable"),
        (ProcessError, "agent_crashed"),
        ((CLIJSONDecodeError, MessageParseError), "protocol_error"),
        (TimeoutError, "timeout"),
    )
    code = next((code for kind, code in kinds if isinstance(exc, kind)), "agent_error")
    return failure(code)


#: `AssistantMessage.error`, the CLI's own label for a failed model call.
_MESSAGE_ERRORS = {
    "authentication_failed": "auth_failed",
    "billing_error": "billing",
    "rate_limit": "rate_limited",
    "invalid_request": "invalid_request",
    "server_error": "overloaded",
    "unknown": "agent_error",
}


#: Said in the failed message's text, whatever its label.
_UNSUPPORTED_MODEL = "does not support this model"


def from_message_error(label: str, text: str = "") -> Failure:
    """`text`: the failed message's own words, which name a few failures its
    label does not (the CLI labels "too old for this model" `unknown`)."""
    if _UNSUPPORTED_MODEL in text.lower():
        return failure("model_unsupported")
    return failure(_MESSAGE_ERRORS.get(label, "agent_error"))


__all__ = ["PROCESS_LEVEL", "Failure", "classify", "failure", "from_message_error"]
