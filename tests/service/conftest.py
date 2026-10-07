from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from urlshort.api import create_app
from urlshort.config import Settings
from urlshort.storage import SqliteRepository


class FakeClock:
    """Controllable time, so expiry tests don't sleep."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings() -> Settings:
    return Settings(base_url="https://sho.rt", admin_api_key="test-admin-key",
                    blocked_domains=frozenset({"evil.example"}))


@pytest.fixture
def repo() -> Iterator[SqliteRepository]:
    r = SqliteRepository()
    yield r
    r.close()


@pytest.fixture
def client(settings: Settings, repo: SqliteRepository, clock: FakeClock) -> TestClient:
    return TestClient(create_app(settings, repo, clock=clock))
