"""Rate-limited warnings: during an outage every request fails the same way; one line per interval (with the
count of suppressed repeats) keeps the signal without flooding the log pipeline when it matters most."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable


class ThrottledLog:
    def __init__(self, logger: logging.Logger, message: str, *, level: int = logging.WARNING, interval: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._log, self._message, self._interval, self._clock = logger, message, interval, clock
        self._level = level
        self._last: float | None = None
        self._suppressed = 0
        self._lock = threading.Lock()

    def __call__(self) -> None:
        with self._lock:
            now = self._clock()
            if self._last is not None and now - self._last < self._interval:
                self._suppressed += 1
                return
            suppressed, self._suppressed, self._last = self._suppressed, 0, now
        self._log.log(self._level, self._message, exc_info=True, extra={"suppressed_since_last": suppressed})
