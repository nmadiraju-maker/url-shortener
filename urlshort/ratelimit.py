"""Per-client rate limiting with GCRA (Generic Cell Rate Algorithm).

GCRA behaves like a token bucket (a steady rate plus a burst allowance) but stores a single number
per client: the "theoretical arrival time" (TAT) at which the client's allowance would be full
again. It is also the algorithm the planned Redis backend will run as one atomic Lua script, so
both backends behave identically.

  emission interval  T   = 60 / rate_per_minute     (seconds between requests at the steady rate)
  a request at `now` is allowed if   now >= max(TAT, now) + T - burst * T
  and then                           TAT  = max(TAT, now) + T

Memory is bounded by `max_keys`. When full, clients whose allowance has completely refilled
(TAT <= now) are dropped first: they are indistinguishable from brand-new clients, so dropping them
changes nothing. Only if every tracked client is active is the least recently used one evicted,
which cannot be abused to reset someone else's limit while they are being limited.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int            # burst size: requests allowed back-to-back from a full allowance
    remaining: int        # requests still allowed right now
    reset_after: float    # seconds until the allowance is completely full again
    retry_after: float    # seconds until the next request would be allowed (0 if allowed)

    def headers(self) -> dict[str, str]:
        """RateLimit-* response headers (IETF draft) plus Retry-After when refused."""
        out = {"RateLimit-Limit": str(self.limit), "RateLimit-Remaining": str(self.remaining),
               "RateLimit-Reset": str(math.ceil(self.reset_after))}
        if not self.allowed:
            out["Retry-After"] = str(max(1, math.ceil(self.retry_after)))
        return out


class RateLimiter(Protocol):
    def acquire(self, key: str) -> Decision: ...


class GcraLimiter:
    def __init__(self, rate_per_minute: int, burst: int, *, clock: Callable[[], float],
                 max_keys: int = 100_000) -> None:
        if rate_per_minute < 1 or burst < 1 or max_keys < 1:
            raise ValueError("rate_per_minute, burst and max_keys must all be at least 1")
        self.limit = burst
        self._interval = 60.0 / rate_per_minute
        self._window = burst * self._interval          # how far TAT may run ahead of now
        self._clock = clock
        self._max_keys = max_keys
        self._tat: OrderedDict[str, float] = OrderedDict()   # key -> TAT, least recently used first
        self._lock = threading.Lock()

    def acquire(self, key: str) -> Decision:
        with self._lock:
            now = self._clock()
            tat = max(self._tat.get(key, now), now)
            new_tat = tat + self._interval
            allow_at = new_tat - self._window
            if now < allow_at:
                return Decision(allowed=False, limit=self.limit, remaining=0,
                                reset_after=tat - now, retry_after=allow_at - now)
            if key not in self._tat and len(self._tat) >= self._max_keys:
                self._make_room(now)
            self._tat[key] = new_tat
            self._tat.move_to_end(key)
            remaining = min(self.limit, int((now - allow_at) / self._interval + 1e-9))
            return Decision(allowed=True, limit=self.limit, remaining=remaining,
                            reset_after=new_tat - now, retry_after=0.0)

    def tracked(self) -> int:
        return len(self._tat)

    def _make_room(self, now: float) -> None:
        idle = [k for k, tat in self._tat.items() if tat <= now]
        for k in idle:
            del self._tat[k]
        if not idle:
            self._tat.popitem(last=False)   # every client is active: evict the least recently used
