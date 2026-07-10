"""Structured logging for the trading system.

Every log record is emitted as one JSON object so the orchestrator's
per-cycle decision trail is machine-parseable, not just human-readable.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

_CONFIGURED = False
_NAIROBI_TZ = ZoneInfo("Africa/Nairobi")


class JsonFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        # Explicit Africa/Nairobi conversion from record.created (a UNIX
        # epoch timestamp, always UTC-based) - NOT the base class's default
        # behavior of formatting via the OS's local clock. Depending on the
        # host machine's own timezone setting for "local time" is fragile
        # and silently wrong the moment the system runs on a differently
        # configured machine; this guarantees EAT everywhere, matching the
        # dashboard's and journal's own explicit conversion.
        return datetime.fromtimestamp(record.created, tz=_NAIROBI_TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if context:
            payload["context"] = context
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Configure root logging once: JSON to console, plus an optional rotating file handler.

    Safe to call multiple times (e.g. once per agent module import) -
    only the first call takes effect.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(JsonFormatter())
    root.addHandler(console_handler)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

    _CONFIGURED = True


def reset_logging_for_tests() -> None:
    """Undo configure_logging() so tests can reconfigure logging cleanly."""
    global _CONFIGURED
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    _CONFIGURED = False
