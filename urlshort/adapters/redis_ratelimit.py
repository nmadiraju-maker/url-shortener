"""Distributed GCRA rate limiting in Redis: the same algorithm as urlshort.ratelimit.GcraLimiter.

One Lua script per request: read the client's theoretical arrival time (TAT), decide, store, set an
expiry, atomically. Time comes from Redis (TIME), so instances with skewed clocks still agree. If Redis
is unavailable the limiter fails OPEN and logs a warning: an outage of the limiter must not take the
service down (the trade-off: no limiting during the outage).
"""

from __future__ import annotations

import logging
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from ..ratelimit import Decision
from ..throttled import ThrottledLog

log = logging.getLogger("urlshort.ratelimit")

GCRA_LUA = """
local interval = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local tat = tonumber(redis.call('GET', KEYS[1]) or now)
if tat < now then tat = now end
local new_tat = tat + interval
local allow_at = new_tat - burst * interval
if now < allow_at then
  return {0, 0, tat - now, allow_at - now}
end
redis.call('SET', KEYS[1], new_tat, 'PX', new_tat - now)
local remaining = math.floor((now - allow_at) / interval)
if remaining > burst then remaining = burst end
return {1, remaining, new_tat - now, 0}
"""


class RedisGcraLimiter:
    def __init__(self, client: Redis, name: str, rate_per_minute: int, burst: int) -> None:
        if rate_per_minute < 1 or burst < 1:
            raise ValueError("rate_per_minute and burst must be at least 1")
        self.limit = burst
        self._interval_ms = max(1, 60_000 // rate_per_minute)
        self._prefix = f"urlshort:rl:{name}:"
        self._script = client.register_script(GCRA_LUA)
        self._warn = ThrottledLog(log, "rate limiter unavailable; allowing requests")

    def acquire(self, key: str) -> Decision:
        try:
            result: Any = self._script(keys=[self._prefix + key], args=[self._interval_ms, self.limit])
        except RedisError:
            self._warn()
            return Decision(allowed=True, limit=self.limit, remaining=self.limit, reset_after=0.0, retry_after=0.0)
        allowed, remaining, reset_ms, retry_ms = (int(x) for x in result)
        return Decision(allowed=bool(allowed), limit=self.limit, remaining=remaining,
                        reset_after=reset_ms / 1000, retry_after=retry_ms / 1000)
