"""Structured logging configuration."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


class _JsonFormatter(logging.Formatter):
    """Minimal JSON-lines formatter; never emits exception objects verbatim."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict) and extra:
            rendered = " ".join(f"{k}={v}" for k, v in sorted(extra.items()))
            return f"{base} | {rendered}"
        return base


def configure_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    """Configure the root logger once. Safe to call repeatedly."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    if json_output:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            _TextFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        )
    root.addHandler(handler)


def log_fields(logger: logging.Logger, level: int, msg: str, **fields: object) -> None:
    """Emit a log line with structured key=value fields attached."""
    logger.log(level, msg, extra={"fields": fields})
