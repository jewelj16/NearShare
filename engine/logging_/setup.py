"""Structured logging setup with session_id correlation.

Provides a logging configuration that attaches a ``session_id`` field
to every log record emitted within the context of a transfer session.
Uses stdlib logging — no third-party dependencies.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any


# Context variable holding the current session_id (if any).
_session_id_var: ContextVar[str | None] = ContextVar(
    "nearshare_session_id", default=None
)


def set_session_id(session_id: str | None) -> None:
    """Set the session_id for the current async context.

    Args:
        session_id: The transfer session ID, or None to clear.
    """
    _session_id_var.set(session_id)


def get_session_id() -> str | None:
    """Return the session_id for the current async context, or None."""
    return _session_id_var.get()


class SessionFilter(logging.Filter):
    """Logging filter that injects ``session_id`` into every log record.

    If no session_id is set in the current context, the field is set
    to ``"-"`` so log formatters can always reference it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = _session_id_var.get() or "-"  # type: ignore[attr-defined]
        return True


def setup_engine_logging(
    level: int = logging.DEBUG,
    fmt: str | None = None,
) -> logging.Logger:
    """Configure and return the ``nearshare.engine`` logger.

    Attaches the SessionFilter so that all log records under
    ``nearshare.engine`` carry a ``session_id`` field.

    Args:
        level: Logging level (default DEBUG).
        fmt:   Custom format string.  Must contain ``%(session_id)s``
               if you want the session ID in output.

    Returns:
        The configured logger.
    """
    if fmt is None:
        fmt = (
            "%(asctime)s [%(levelname)s] "
            "[session=%(session_id)s] "
            "%(name)s: %(message)s"
        )

    logger = logging.getLogger("nearshare.engine")
    logger.setLevel(level)

    # avoid duplicate handlers on repeated calls
    if not logger.handlers:
        handler = logging.FileHandler("logs.txt")
        handler.setLevel(level)
        formatter = logging.Formatter(fmt)
        handler.setFormatter(formatter)
        handler.addFilter(SessionFilter())
        logger.addHandler(handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under ``nearshare.engine``.

    The returned logger inherits the SessionFilter from the parent.

    Args:
        name: Dot-separated name appended to ``nearshare.engine``.

    Returns:
        A logging.Logger instance.
    """
    return logging.getLogger(f"nearshare.engine.{name}")
