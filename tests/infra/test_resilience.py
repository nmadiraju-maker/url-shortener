"""Found by the chaos drills: fail-fast 503s and a circuit breaker when Postgres is down."""
import time

import pytest

from urlshort.adapters.postgres import PostgresRepository
from urlshort.errors import StorageUnavailable


def test_postgres_outage_fails_fast_with_503() -> None:
    repo = PostgresRepository("postgresql://urlshort:urlshort@127.0.0.1:1/urlshort", auto_migrate=False,
                              min_size=0, timeout=0.5, cooldown=0.3)
    started = time.perf_counter()
    with pytest.raises(StorageUnavailable):
        repo.get_link("abc1234")
    assert time.perf_counter() - started < 3                 # not the old 30-second hang
    started = time.perf_counter()
    for _ in range(100):                                     # circuit open: every call fails at once
        with pytest.raises(StorageUnavailable):
            repo.get_link("abc1234")
    assert time.perf_counter() - started < 0.2
    time.sleep(0.35)                                         # cooldown over: the next call tries again (and waits)
    started = time.perf_counter()
    with pytest.raises(StorageUnavailable):
        repo.get_link("abc1234")
    assert time.perf_counter() - started >= 0.4
    assert repo.ping() is False
    repo.close()
