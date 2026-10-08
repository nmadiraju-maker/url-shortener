"""Infrastructure tests need real servers: set URLSHORT_TEST_DATABASE_URL and/or URLSHORT_TEST_REDIS_URL
(CI's infrastructure job does; locally: `make ci-infra`, which starts them with docker compose)."""
import os
from collections.abc import Iterator

import pytest

PG_URL = os.environ.get("URLSHORT_TEST_DATABASE_URL", "")
REDIS_URL = os.environ.get("URLSHORT_TEST_REDIS_URL", "")


@pytest.fixture(scope="session")
def pg_url() -> str:
    if not PG_URL:
        pytest.skip("URLSHORT_TEST_DATABASE_URL not set")
    return PG_URL


@pytest.fixture
def pg(pg_url: str) -> Iterator["PostgresRepository"]:  # type: ignore[name-defined]  # noqa: F821
    from urlshort.adapters.postgres import PostgresRepository
    repo = PostgresRepository(pg_url, max_size=50)
    with repo._pool.connection() as conn:
        conn.execute("TRUNCATE links, clicks, audit_log RESTART IDENTITY CASCADE")
    yield repo
    repo.close()


@pytest.fixture
def redis_client() -> Iterator["Redis"]:  # type: ignore[name-defined]  # noqa: F821
    if not REDIS_URL:
        pytest.skip("URLSHORT_TEST_REDIS_URL not set")
    from redis import Redis
    client = Redis.from_url(REDIS_URL)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()
