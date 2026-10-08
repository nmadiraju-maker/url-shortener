"""Reliability metrics: success rate, retries, rollbacks, MTTR, stage and end-to-end latency."""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _StageStats:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    retries: int = 0
    rollbacks: int = 0
    fallbacks: int = 0
    reworks: int = 0
    durations: list[float] = field(default_factory=list)
    failing_since: float | None = None
    recovery_times: list[float] = field(default_factory=list)


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


class Metrics:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.stages: dict[str, _StageStats] = {}
        self.started_at = clock()
        self.finished_at: float | None = None
        self.counters: dict[str, int] = {}

    def _s(self, stage: str) -> _StageStats:
        return self.stages.setdefault(stage, _StageStats())

    def incr(self, name: str, by: int = 1) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + by

    def attempt(self, stage: str, duration: float, ok: bool) -> None:
        with self._lock:
            s = self._s(stage)
            s.attempts += 1
            s.durations.append(duration)
            now = self._clock()
            if ok:
                s.successes += 1
                if s.failing_since is not None:
                    s.recovery_times.append(now - s.failing_since)
                    s.failing_since = None
            else:
                s.failures += 1
                if s.failing_since is None:
                    s.failing_since = now - duration

    def mark(self, stage: str, kind: str) -> None:
        with self._lock:
            s = self._s(stage)
            setattr(s, kind, getattr(s, kind) + 1)

    def finish(self) -> None:
        self.finished_at = self._clock()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            attempts = sum(s.attempts for s in self.stages.values())
            successes = sum(s.successes for s in self.stages.values())
            recoveries = [t for s in self.stages.values() for t in s.recovery_times]
            end = self.finished_at if self.finished_at is not None else self._clock()
            return {
                "attempts": attempts,
                "attempt_success_rate": round(successes / attempts, 3) if attempts else None,
                "retries": sum(s.retries for s in self.stages.values()),
                "rollbacks": sum(s.rollbacks for s in self.stages.values()),
                "fallbacks": sum(s.fallbacks for s in self.stages.values()),
                "reworks": sum(s.reworks for s in self.stages.values()),
                "retry_frequency": _ratio(sum(s.retries for s in self.stages.values()), attempts),
                "rollback_frequency": _ratio(sum(s.rollbacks for s in self.stages.values()), attempts),
                "incidents_recovered": len(recoveries),
                "mttr_seconds": round(sum(recoveries) / len(recoveries), 3) if recoveries else None,
                "end_to_end_latency_seconds": round(end - self.started_at, 3),
                "counters": dict[str, Any](self.counters),
                "per_stage": {k: {"attempts": v.attempts, "successes": v.successes, "failures": v.failures,
                                  "retries": v.retries, "rollbacks": v.rollbacks, "fallbacks": v.fallbacks,
                                  "reworks": v.reworks, "total_seconds": round(sum(v.durations), 3)}
                              for k, v in self.stages.items()},
            }
