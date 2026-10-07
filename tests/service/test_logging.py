import io
import json
import logging
import logging.config
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from urlshort.logging_setup import (
    REDACTED,
    REQUEST_ID_ATTR,
    ContextFilter,
    JsonFormatter,
    RedactingFilter,
    StructuredQueueHandler,
    configure_logging,
    request_id_var,
    sampled,
    shutdown_logging,
)

ROOT = Path(__file__).resolve().parents[2]


def make_record(msg: str = "created %s", args: tuple[object, ...] = ("abc",), **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("urlshort.test", logging.INFO, __file__, 1, msg, args, None)
    record.__dict__.update(extra)
    return record


def failing_record() -> logging.LogRecord:
    try:
        raise ValueError("bad")
    except ValueError:
        return logging.LogRecord("urlshort.x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())


# ---------------- formatter
def test_core_fields_and_live_request_id() -> None:
    token = request_id_var.set("rid-1")
    try:
        out = json.loads(JsonFormatter().format(make_record()))
    finally:
        request_id_var.reset(token)
    assert out["msg"] == "created abc" and out["level"] == "INFO" and out["logger"] == "urlshort.test"
    assert out["request_id"] == "rid-1" and out["ts"].endswith("+00:00") and "ctx" not in out


def test_captured_request_id_wins_over_live_value() -> None:
    out = json.loads(JsonFormatter().format(make_record(**{REQUEST_ID_ATTR: "captured"})))
    assert out["request_id"] == "captured" and "ctx" not in out


def test_extras_are_nested_and_cannot_overwrite_core_fields() -> None:
    out = json.loads(JsonFormatter().format(make_record(code="abc", level="FAKE", request_id="spoofed")))
    assert out["level"] == "INFO" and out["request_id"] == "-"
    assert out["ctx"] == {"code": "abc", "level": "FAKE", "request_id": "spoofed"}


def test_exceptions_from_live_or_frozen_tracebacks() -> None:
    live = failing_record()
    assert "ValueError: bad" in json.loads(JsonFormatter().format(live))["exc"]
    frozen = make_record()
    frozen.exc_text = "Traceback: frozen"
    assert json.loads(JsonFormatter().format(frozen))["exc"] == "Traceback: frozen"


def test_unserialisable_extras_become_text() -> None:
    assert json.loads(JsonFormatter().format(make_record(when=object())))["ctx"]["when"].startswith("<object")


# ---------------- filters and queue handler
def test_redaction_at_any_depth_case_insensitive() -> None:
    record = make_record(Token="t-123", code="abc", headers={"Authorization": "Bearer x", "accept": "json"},
                         items=[{"password": "p"}, "plain"], who=("ok", {"email": "a@b.example"}))
    RedactingFilter({"token", "authorization", "password", "email"}).filter(record)
    assert record.__dict__["Token"] == REDACTED and record.__dict__["code"] == "abc"
    assert record.__dict__["headers"] == {"Authorization": REDACTED, "accept": "json"}
    assert record.__dict__["items"] == [{"password": REDACTED}, "plain"]
    assert record.__dict__["who"] == ["ok", {"email": REDACTED}]


def test_context_filter_captures_request_id() -> None:
    record = make_record()
    token = request_id_var.set("rid-ctx")
    try:
        assert ContextFilter().filter(record)
    finally:
        request_id_var.reset(token)
    assert getattr(record, REQUEST_ID_ATTR) == "rid-ctx"


def test_queue_handler_freezes_a_copy_and_leaves_the_original_intact() -> None:
    original = failing_record()
    original.msg, original.args = "failed %s", ("now",)
    queued = StructuredQueueHandler(io.StringIO()).prepare(original)  # type: ignore[arg-type]
    assert queued is not original
    assert queued.msg == "failed now" and queued.args is None and queued.exc_info is None
    assert queued.exc_text is not None and "ValueError: bad" in queued.exc_text
    assert original.exc_info is not None and original.args == ("now",)     # other handlers still see it
    plain = StructuredQueueHandler(io.StringIO()).prepare(make_record())  # type: ignore[arg-type]
    assert plain.exc_text is None


# ---------------- sampling
def test_sampling_is_deterministic_and_proportional() -> None:
    ids = [f"req-{i}" for i in range(4000)]
    assert all(sampled(i, 1.0) for i in ids) and not any(sampled(i, 0.0) for i in ids)
    kept = [i for i in ids if sampled(i, 0.25)]
    assert 0.2 < len(kept) / len(ids) < 0.3
    assert kept == [i for i in ids if sampled(i, 0.25)]                # same decision every time


# ---------------- end to end through the background thread
@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    shutdown_logging()
    buf = io.StringIO()
    configure_logging("INFO", {"token"}, stream=buf)
    yield buf
    shutdown_logging()


def test_request_id_and_redaction_survive_the_background_thread(captured: io.StringIO) -> None:
    log = logging.getLogger("urlshort.test")
    token = request_id_var.set("rid-thread")
    try:
        log.info("created %s", "abc", extra={"code": "abc", "token": "s3cret", "client_ip": "203.0.113.7"})
        log.debug("hidden at INFO")
    finally:
        request_id_var.reset(token)
    shutdown_logging()                                                 # flushes the queue
    (line,) = [json.loads(x) for x in captured.getvalue().splitlines()]
    assert line["request_id"] == "rid-thread"                         # would be "-" if read on the listener thread
    assert line["msg"] == "created abc"
    assert line["ctx"] == {"code": "abc", "token": REDACTED, "client_ip": REDACTED}
    assert "s3cret" not in captured.getvalue() and "203.0.113.7" not in captured.getvalue()


def test_reconfiguring_updates_level_and_keys_without_new_handlers(captured: io.StringIO) -> None:
    configure_logging("WARNING", {"code"})
    logger = logging.getLogger("urlshort")
    assert logger.level == logging.WARNING
    assert sum(isinstance(h, StructuredQueueHandler) for h in logger.handlers) == 1
    logging.getLogger("urlshort.test").warning("w", extra={"code": "abc", "token": "t"})
    shutdown_logging()
    ctx = json.loads(captured.getvalue())["ctx"]
    assert ctx == {"code": REDACTED, "token": REDACTED}


def test_invalid_level_and_idle_shutdown() -> None:
    with pytest.raises(ValueError, match="log level"):
        configure_logging("LOUD")
    shutdown_logging()
    shutdown_logging()                                                 # safe when nothing is running


# ---------------- uvicorn's own lines
def test_uvicorn_log_config_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    logging.config.dictConfig(json.loads((ROOT / "config" / "uvicorn-logging.json").read_text()))
    logging.getLogger("uvicorn.error").info("Started server process [%d]", 42,
                                            extra={"color_message": "Started \x1b[1m%d\x1b[0m"})
    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert line["logger"] == "uvicorn.error" and line["msg"] == "Started server process [42]"
    assert "ctx" not in line                                           # colour-coded duplicate dropped
