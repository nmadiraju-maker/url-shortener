"""Structured JSON logging with request correlation IDs.

Every line is one JSON object. Fields passed via `extra=` are nested under "ctx", so they can never
overwrite the core fields (ts, level, logger, msg, request_id).
"""

from __future__ import annotations

import contextvars
import json
import logging
from datetime import UTC, datetime

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_BUILTIN = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        ctx = {k: v for k, v in record.__dict__.items() if k not in _BUILTIN}
        if ctx:
            payload["ctx"] = ctx
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Attach one JSON handler to the 'urlshort' logger. Safe to call repeatedly."""
    logger = logging.getLogger("urlshort")
    logger.setLevel(level)
    if any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
