from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from urlshort.ratelimit import Decision, GcraLimiter


class Mono:
    """Controllable monotonic clock (seconds)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_burst_then_refused_with_retry_after() -> None:
    clock = Mono()
    lim = GcraLimiter(60, 3, clock=clock)                # 1 request/second steady, burst of 3
    results = [lim.acquire("a") for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results] == [2, 1, 0, 0]
    refused = results[3]
    assert refused.retry_after == pytest.approx(1.0) and refused.reset_after == pytest.approx(3.0)
    assert refused.headers() == {"RateLimit-Limit": "3", "RateLimit-Remaining": "0", "RateLimit-Reset": "3",
                                 "Retry-After": "1"}


def test_steady_rate_refills_one_request_per_interval() -> None:
    clock = Mono()
    lim = GcraLimiter(60, 2, clock=clock)
    assert lim.acquire("a").allowed and lim.acquire("a").allowed and not lim.acquire("a").allowed
    clock.t += 0.5
    assert not lim.acquire("a").allowed                  # half an interval is not enough
    clock.t += 0.5
    assert lim.acquire("a").allowed and not lim.acquire("a").allowed


def test_idle_client_gets_full_burst_back() -> None:
    clock = Mono()
    lim = GcraLimiter(60, 3, clock=clock)
    for _ in range(3):
        lim.acquire("a")
    clock.t += 3.0
    assert [lim.acquire("a").allowed for _ in range(4)] == [True, True, True, False]


def test_refusals_do_not_consume_allowance() -> None:
    clock = Mono()
    lim = GcraLimiter(60, 1, clock=clock)
    lim.acquire("a")
    for _ in range(50):                                  # hammering while limited...
        lim.acquire("a")
    clock.t += 1.0
    assert lim.acquire("a").allowed                      # ...does not push the next slot further away


def test_clients_are_independent() -> None:
    lim = GcraLimiter(60, 1, clock=Mono())
    assert lim.acquire("a").allowed and not lim.acquire("a").allowed
    assert lim.acquire("b").allowed


def test_headers_round_up_and_omit_retry_after_when_allowed() -> None:
    d = Decision(allowed=True, limit=10, remaining=9, reset_after=0.2, retry_after=0.0)
    assert d.headers() == {"RateLimit-Limit": "10", "RateLimit-Remaining": "9", "RateLimit-Reset": "1"}
    assert Decision(False, 10, 0, 5.0, 0.01).headers()["Retry-After"] == "1"   # never "0"


@pytest.mark.parametrize("rate,burst,keys", [(0, 1, 1), (1, 0, 1), (1, 1, 0)])
def test_invalid_parameters(rate: int, burst: int, keys: int) -> None:
    with pytest.raises(ValueError):
        GcraLimiter(rate, burst, clock=Mono(), max_keys=keys)


def test_full_table_drops_idle_clients_first() -> None:
    clock = Mono()
    lim = GcraLimiter(60, 1, clock=clock, max_keys=3)
    lim.acquire("idle-1")
    lim.acquire("idle-2")
    clock.t += 5.0                                       # idle clients' allowances are full again
    lim.acquire("active")
    lim.acquire("newcomer")                              # table full: both idle entries are dropped
    assert lim.tracked() == 2
    assert not lim.acquire("active").allowed             # the active client's limit was NOT reset


def test_full_table_of_active_clients_evicts_least_recently_used() -> None:
    clock = Mono()
    lim = GcraLimiter(60, 1, clock=clock, max_keys=2)
    lim.acquire("old")
    lim.acquire("recent")
    lim.acquire("newcomer")                              # nobody idle: "old" (LRU) is evicted
    assert lim.tracked() == 2
    assert not lim.acquire("recent").allowed             # still limited: kept
    assert lim.acquire("old").allowed                    # evicted, so it starts fresh


def test_exact_under_concurrency() -> None:
    lim = GcraLimiter(1, 10, clock=Mono())               # 10 allowed, then nothing for a minute
    barrier = Barrier(50)

    def hit(_: int) -> bool:
        barrier.wait()
        return lim.acquire("same-client").allowed
    with ThreadPoolExecutor(max_workers=50) as pool:
        assert sum(pool.map(hit, range(50))) == 10
