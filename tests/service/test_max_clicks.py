"""Per-link click caps (AC-MAXCLICKS-*): atomic enforcement, bots, fail-closed storage, schema v3."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from urlshort.config import Settings
from urlshort.errors import InvalidInput, LinkExhausted, StorageUnavailable
from urlshort.service import ShortenerService
from urlshort.storage import SCHEMA_VERSION, Click, SqliteRepository
from urlshort.validation import validate_max_clicks

from .helpers import FakeClock

CHROME = {"user-agent": "Mozilla/5.0 Chrome/126.0"}
BOT = {"user-agent": "Googlebot/2.1"}


def test_cap_is_enforced_with_link_exhausted(client: TestClient) -> None:
    """AC-MAXCLICKS-1"""
    link = client.post("/api/v1/links", json={"url": "https://example.com/offer", "max_clicks": 2}).json()
    assert link["max_clicks"] == 2
    for _ in range(2):
        assert client.get(f"/{link['code']}", headers=CHROME, follow_redirects=False).status_code == 307
    refused = client.get(f"/{link['code']}", headers=CHROME, follow_redirects=False)
    assert refused.status_code == 410 and refused.json()["error"]["code"] == "link_exhausted"
    assert client.get(f"/api/v1/links/{link['code']}").json()["click_count"] == 2


def test_cap_holds_under_concurrency(repo: SqliteRepository, clock: FakeClock) -> None:
    """AC-MAXCLICKS-1: 40 simultaneous redirects on a 10-click link -> exactly 10 succeed."""
    svc = ShortenerService(repo, Settings(), clock=clock)
    code = svc.shorten("https://example.com/race", owner="o", max_clicks=10).link.code
    barrier = Barrier(40)

    def hit(_: int) -> bool:
        barrier.wait()
        try:
            svc.resolve(code, user_agent="Mozilla/5.0 Chrome/126.0")
            return True
        except LinkExhausted:
            return False
    with ThreadPoolExecutor(max_workers=40) as pool:
        assert sum(pool.map(hit, range(40))) == 10
    link = repo.get_link(code)
    assert link is not None and link.click_count == 10


@pytest.mark.parametrize("value", [0, -1, 1_000_001])
def test_out_of_range_caps_are_rejected(client: TestClient, value: int) -> None:
    """AC-MAXCLICKS-2"""
    resp = client.post("/api/v1/links", json={"url": "https://example.com", "max_clicks": value})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "invalid_input"


def test_service_validates_caps_too() -> None:
    """AC-MAXCLICKS-2: the rule lives in the service, not only in the HTTP model."""
    assert validate_max_clicks(None) is None and validate_max_clicks(5) == 5
    with pytest.raises(InvalidInput, match="between 1 and 1000000"):
        validate_max_clicks(0)


def test_bots_do_not_consume_the_cap(client: TestClient) -> None:
    """AC-MAXCLICKS-3"""
    code = client.post("/api/v1/links", json={"url": "https://example.com/cap", "max_clicks": 1}).json()["code"]
    for _ in range(3):
        assert client.get(f"/{code}", headers=BOT, follow_redirects=False).status_code == 307
    assert client.get(f"/{code}", headers=CHROME, follow_redirects=False).status_code == 307
    assert client.get(f"/{code}", headers=BOT, follow_redirects=False).status_code == 410


def test_capped_links_fail_closed_when_storage_breaks(repo: SqliteRepository, clock: FakeClock) -> None:
    svc = ShortenerService(repo, Settings(), clock=clock)
    code = svc.shorten("https://example.com/c", owner="o", max_clicks=5).link.code

    def broken(click: Click) -> bool:
        raise sqlite3.OperationalError("database is locked")
    repo.record_click_with_limit = broken  # type: ignore[method-assign]
    with pytest.raises(StorageUnavailable):
        svc.resolve(code)


def test_failed_capped_write_rolls_back_atomically(repo: SqliteRepository, clock: FakeClock) -> None:
    svc = ShortenerService(repo, Settings(), clock=clock)
    code = svc.shorten("https://example.com/r", owner="o", max_clicks=5).link.code
    repo._conn.execute("DROP TABLE clicks")
    with pytest.raises(StorageUnavailable):
        svc.resolve(code)
    link = repo.get_link(code)
    assert link is not None and link.click_count == 0


def test_capped_links_are_never_reused_for_plain_requests(repo: SqliteRepository, clock: FakeClock) -> None:
    svc = ShortenerService(repo, Settings(), clock=clock)
    capped = svc.shorten("https://example.com/same", owner="o", max_clicks=3)
    plain = svc.shorten("https://example.com/same", owner="o")
    again = svc.shorten("https://example.com/same", owner="o", max_clicks=3)
    assert plain.created and plain.link.code != capped.link.code and plain.link.max_clicks is None
    assert again.created and again.link.code not in (capped.link.code, plain.link.code)
    assert svc.shorten("https://example.com/same", owner="o").link.code == plain.link.code


def test_uncapped_links_still_fail_open(repo: SqliteRepository, clock: FakeClock) -> None:
    svc = ShortenerService(repo, Settings(), clock=clock)
    code = svc.shorten("https://example.com/u", owner="o").link.code
    repo._conn.execute("DROP TABLE clicks")
    assert svc.resolve(code) == "https://example.com/u"


def test_version_2_database_is_upgraded_to_the_current_version(tmp_path: Path) -> None:
    path = tmp_path / "v2.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE links (code TEXT PRIMARY KEY, target_url TEXT NOT NULL, owner TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT, is_active INTEGER NOT NULL DEFAULT 1,
            click_count INTEGER NOT NULL DEFAULT 0, stats_token_hash TEXT);
        INSERT INTO links VALUES ('old0001', 'https://example.com', 'o', '2026-01-01T00:00:00+00:00', NULL, 1, 4, NULL);
        PRAGMA user_version = 2;
    """)
    conn.close()
    r = SqliteRepository(str(path))
    old = r.get_link("old0001")
    assert old is not None and old.click_count == 4 and old.max_clicks is None
    assert r._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION >= 3   # current, whatever it is
    r.close()
