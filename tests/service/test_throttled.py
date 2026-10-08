"""Found by the chaos drills: an outage must not turn every request into a log line."""
import logging

import pytest

from urlshort.throttled import ThrottledLog


def test_repeated_failures_log_once_per_interval(caplog: pytest.LogCaptureFixture) -> None:
    now = [0.0]
    warn = ThrottledLog(logging.getLogger("urlshort.test"), "cache unavailable", interval=30, clock=lambda: now[0])
    with caplog.at_level(logging.WARNING, logger="urlshort.test"):
        for _ in range(1000):                       # 1000 failing requests during an outage
            warn()
        now[0] = 31
        warn()
    records = [r for r in caplog.records if r.message == "cache unavailable"]
    assert len(records) == 2 and records[1].__dict__["suppressed_since_last"] == 999
