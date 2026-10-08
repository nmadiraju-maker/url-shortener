"""Structured, non-blocking JSON logging with request correlation IDs and redaction.

Every line is one JSON object. Fields passed via `extra=` are nested under "ctx", so they can never
overwrite the core fields (ts, level, logger, msg, request_id).

Pipeline for the 'urlshort' logger:

    log call (request thread)
      -> QueueHandler + filters      capture request ID, redact sensitive fields, freeze the message
      -> in-memory queue
      -> QueueListener (background)  JsonFormatter -> stderr

The filters run on the caller's thread. That matters: the request ID lives in a context variable of
the request's thread, so reading it later on the listener thread would always give "-". Redaction
also happens before the record leaves the caller, so a secret never reaches the queue at all.
"""

from __future__ import annotations

import atexit
import contextvars
import copy
import json
import logging
import logging.handlers
import queue
import zlib
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, TextIO

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

REQUEST_ID_ATTR = "urlshort_request_id"
TRACE_ID_ATTR = "urlshort_trace_id"
REDACTED = "[REDACTED]"
DEFAULT_REDACT_KEYS = frozenset({"authorization", "x_api_key", "api_key", "password", "secret", "token",
                                 "stats_token", "x_stats_token", "client_ip", "ip", "email", "cookie"})
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_BUILTIN = (set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)
            | {"message", "asctime", "taskName", REQUEST_ID_ATTR, TRACE_ID_ATTR,
               "color_message"})   # uvicorn's copy of msg with terminal colour codes: noise in a log store


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            # Captured on the caller's thread when queued; read live otherwise (e.g. uvicorn's lines).
            "request_id": getattr(record, REQUEST_ID_ATTR, None) or request_id_var.get(),
        }
        trace_id = getattr(record, TRACE_ID_ATTR, None)
        if trace_id:
            payload["trace_id"] = trace_id
        ctx = {k: v for k, v in record.__dict__.items() if k not in _BUILTIN}
        if ctx:
            payload["ctx"] = ctx
        if record.exc_text:
            payload["exc"] = record.exc_text
        elif record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _redact(value: Any, keys: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {k: REDACTED if str(k).lower() in keys else _redact(v, keys) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, keys) for v in value]
    return value


class RedactingFilter(logging.Filter):
    """Replace values of sensitive extra fields (matched case-insensitively, at any depth).

    Free-text messages are not scanned: pass data as structured extras, never interpolated into msg.
    """

    def __init__(self, keys: Iterable[str]) -> None:
        super().__init__()
        self.keys = frozenset(k.lower() for k in keys)

    def filter(self, record: logging.LogRecord) -> bool:
        for key in [k for k in record.__dict__ if k not in _BUILTIN]:
            record.__dict__[key] = REDACTED if key.lower() in self.keys else _redact(record.__dict__[key], self.keys)
        return True


class ContextFilter(logging.Filter):
    """Capture the request ID while still on the caller's thread."""

    def filter(self, record: logging.LogRecord) -> bool:
        setattr(record, REQUEST_ID_ATTR, request_id_var.get())
        from .observability import current_trace_id  # local import: logging is set up before tracing
        setattr(record, TRACE_ID_ATTR, current_trace_id())
        return True


class StructuredQueueHandler(logging.handlers.QueueHandler):
    """Queue a copy of the record with its message and traceback frozen, so the listener thread
    formats exactly what the caller logged.

    It must be a copy: the same record object is passed on to every other handler (parent loggers,
    pytest's caplog, error-tracking integrations), which still need the original traceback.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        queued = copy.copy(record)
        queued.msg = record.getMessage()
        queued.args = None
        if record.exc_info:
            queued.exc_text = logging.Formatter().formatException(record.exc_info)
            queued.exc_info = None
        return queued


def sampled(request_id: str, rate: float) -> bool:
    """Deterministic sampling by request ID: no randomness, and every service that sees the same
    request ID makes the same keep/drop decision."""
    if rate >= 1.0:
        return True
    return zlib.crc32(request_id.encode()) / 0xFFFFFFFF < rate


class _State:
    listener: logging.handlers.QueueListener | None = None
    handler: StructuredQueueHandler | None = None
    redactor: RedactingFilter | None = None


def configure_logging(level: str = "INFO", redact_keys: Iterable[str] = DEFAULT_REDACT_KEYS,
                      stream: TextIO | None = None) -> None:
    """Set up the 'urlshort' logger once; later calls only update the level and redaction keys.

    `stream` defaults to stderr; tests pass their own to read what the background thread wrote.
    `redact_keys` always ADD to DEFAULT_REDACT_KEYS: no caller can switch off the built-in redaction.
    """
    keys = frozenset(k.lower() for k in redact_keys) | DEFAULT_REDACT_KEYS
    if level not in LEVELS:
        raise ValueError(f"log level must be one of {LEVELS}, got {level!r}")
    logger = logging.getLogger("urlshort")
    logger.setLevel(level)
    if _State.handler is not None and _State.redactor is not None:
        _State.redactor.keys = keys
        return
    records: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    output = logging.StreamHandler(stream)
    output.setFormatter(JsonFormatter())
    _State.redactor = RedactingFilter(keys)
    _State.handler = StructuredQueueHandler(records)
    _State.handler.addFilter(ContextFilter())
    _State.handler.addFilter(_State.redactor)
    logger.addHandler(_State.handler)
    _State.listener = logging.handlers.QueueListener(records, output, respect_handler_level=True)
    _State.listener.start()
    atexit.register(shutdown_logging)


def shutdown_logging() -> None:
    """Flush queued records and stop the background thread (also registered with atexit)."""
    if _State.listener is not None:
        _State.listener.stop()
        _State.listener = None
    if _State.handler is not None:
        logging.getLogger("urlshort").removeHandler(_State.handler)
        _State.handler = None
        _State.redactor = None
