"""Logging: one configured root, one request id, two output shapes.

The service ran on `print()`. That is fine until the first argument about a
missed mark, at which point somebody has to answer "what happened at 09:14 on
Tuesday for student 20512345" from a Heroku log stream with no timestamps, no
levels and no way to tie three lines together into one request.

So: a request id generated (or accepted) at the edge, carried in a context
variable so every log line inside that request is stamped with it, returned to
the client in `X-Request-ID`, and quoted in error responses. When a student
reads that id off their screen, the whole request is one grep away.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def set_request_id(value: str) -> None:
    _request_id.set(value)


def get_request_id() -> str:
    return _request_id.get()


class _RequestIdFilter(logging.Filter):
    """Attach the current request id to every record, so formatters can use it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, for a log platform that indexes fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key in ("status", "method", "path", "duration_ms", "student_id", "actor"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure(level: str = "INFO", *, as_json: bool = False) -> None:
    """Install handlers on the root logger. Idempotent — safe on reload."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        _JsonFormatter() if as_json
        else logging.Formatter("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
                               datefmt="%H:%M:%S")
    )
    root.addHandler(handler)

    # uvicorn installs its own; let ours do the work so the shape is consistent.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
