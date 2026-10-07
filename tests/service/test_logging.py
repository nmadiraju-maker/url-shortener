import json
import logging
import sys

from urlshort.logging_setup import JsonFormatter, configure_logging, request_id_var


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("urlshort.test", logging.INFO, __file__, 1, "created %s", ("abc",), None)
    record.__dict__.update(extra)
    return record


def test_core_fields_and_request_id() -> None:
    token = request_id_var.set("rid-1")
    try:
        out = json.loads(JsonFormatter().format(make_record()))
    finally:
        request_id_var.reset(token)
    assert out["msg"] == "created abc" and out["level"] == "INFO" and out["logger"] == "urlshort.test"
    assert out["request_id"] == "rid-1" and out["ts"].endswith("+00:00") and "ctx" not in out


def test_extras_are_nested_and_cannot_overwrite_core_fields() -> None:
    out = json.loads(JsonFormatter().format(make_record(code="abc", level="FAKE", request_id="spoofed")))
    assert out["level"] == "INFO" and out["request_id"] == "-"
    assert out["ctx"] == {"code": "abc", "level": "FAKE", "request_id": "spoofed"}


def test_exceptions_and_unserialisable_values() -> None:
    try:
        raise ValueError("bad")
    except ValueError:
        record = logging.LogRecord("urlshort.x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    record.__dict__["when"] = object()
    out = json.loads(JsonFormatter().format(record))
    assert "ValueError: bad" in out["exc"] and out["ctx"]["when"].startswith("<object")


def test_configure_logging_is_idempotent() -> None:
    configure_logging("DEBUG")
    configure_logging("INFO")
    logger = logging.getLogger("urlshort")
    assert sum(isinstance(h.formatter, JsonFormatter) for h in logger.handlers) == 1
    assert logger.level == logging.INFO
