"""Structured logging via structlog, bridged from the stdlib.

``request_id`` is bound per-request by :class:`app.api.middleware.RequestContextMiddleware`
through a contextvar, so every log line emitted while handling a request carries it.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog
from structlog.typing import EventDict, WrappedLogger

from app.security.redact import redact_event

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

_state = {"configured": False}


def _add_request_id(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    rid = request_id_ctx.get()
    if rid is not None:
        event_dict.setdefault("request_id", rid)
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    if _state["configured"]:
        return

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _add_request_id,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        # The traceback becomes a string here, before the scrub (task 6.3).
        # Left to the renderer it was printed after it, unredacted: the
        # console's rich traceback even listed each frame's local variables
        # (a DSN, a request body, a plaintext being sealed), and the JSON
        # log had no traceback at all, only `"exc_info": true`.
        structlog.processors.format_exc_info,
        # Last: masks credentials in whatever the processors above merged in
        # (plan §8, logs never carry secrets).
        redact_event,
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(
            colors=sys.stderr.isatty(), exception_formatter=structlog.dev.plain_traceback
        )
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )

    # A Windows console is cp1252 by default: one character outside it (arq
    # logs a "→" per job) raised UnicodeEncodeError inside logging, and the
    # line was lost behind a "--- Logging error ---" traceback.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for noisy in ("uvicorn", "uvicorn.error"):
        logging.getLogger(noisy).handlers = []
        logging.getLogger(noisy).propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    _state["configured"] = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


__all__ = ["configure_logging", "get_logger", "request_id_ctx"]
