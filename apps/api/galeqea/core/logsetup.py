"""Structured logging.

One configuration routes *all* stdlib logging (GaleQEA, uvicorn, sqlalchemy…) and
structlog through the same renderer: human-readable on a terminal, JSON everywhere
else (containers, CI, a log shipper). Every line carries the request id bound by the
request-id middleware, so a single request's logs are greppable end to end.
"""

from __future__ import annotations

import logging
import sys

import structlog

from ..config import settings

_configured = False


def _use_json() -> bool:
    fmt = (settings.log_format or "auto").lower()
    if fmt == "json":
        return True
    if fmt == "console":
        return False
    # auto: JSON unless we're attached to an interactive terminal.
    return not sys.stderr.isatty()


def configure_logging() -> None:
    """Idempotently install the structlog + stdlib logging pipeline."""
    global _configured
    if _configured:
        return
    _configured = True

    shared = [
        structlog.contextvars.merge_contextvars,       # pulls in the bound request id
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer = (structlog.processors.JSONRenderer() if _use_json()
                else structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO))


def bind_request_id(request_id: str) -> None:
    structlog.contextvars.bind_contextvars(request_id=request_id)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str = "galeqea"):
    return structlog.get_logger(name)
