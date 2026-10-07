from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from urlshort.api import create_app
from urlshort.config import Settings
from urlshort.storage import SqliteRepository

from .helpers import FakeClock


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
