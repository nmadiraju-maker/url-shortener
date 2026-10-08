import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from urlshort.errors import AliasConflict
from urlshort.storage import MIGRATIONS, SCHEMA_VERSION, SET_SCHEMA_VERSION, Click, Link, SqliteRepository

T0 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo() -> Iterator[SqliteRepository]:
    r = SqliteRepository()
    yield r
    r.close()


def link(code: str = "abc1234", url: str = "https://example.com/a", owner: str = "team-a", *,
         created: datetime = T0, expires: datetime | None = None, active: bool = True) -> Link:
    return Link(code=code, target_url=url, owner=owner, created_at=created, expires_at=expires,
                is_active=active, click_count=0)


def click(code: str = "abc1234", *, bot: bool = False, at: datetime = T0) -> Click:
    return Click(code=code, ts=at, referrer_host="news.example", agent_family="bot" if bot else "chrome",
                 is_bot=bot, ip_id=-42, ip_key_id="2026-01-15")


# ---------------- links
def test_insert_and_get_round_trip(repo: SqliteRepository) -> None:
    original = link(expires=T0 + timedelta(hours=1))
    repo.insert_link(original)
    assert repo.get_link("abc1234") == original
    assert repo.get_link("missing") is None


def test_timestamps_are_stored_as_utc(repo: SqliteRepository) -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    repo.insert_link(link(created=datetime(2026, 1, 15, 17, 30, tzinfo=ist)))
    stored = repo.get_link("abc1234")
    assert stored is not None and stored.created_at == T0 and stored.created_at.utcoffset() == timedelta(0)


def test_naive_timestamps_are_refused(repo: SqliteRepository) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.insert_link(link(created=datetime(2026, 1, 15, 12, 0)))


def test_duplicate_code_is_alias_conflict(repo: SqliteRepository) -> None:
    repo.insert_link(link())
    with pytest.raises(AliasConflict, match="already exists"):
        repo.insert_link(link(url="https://example.com/other"))


def test_other_integrity_errors_are_not_disguised_as_conflicts(repo: SqliteRepository) -> None:
    broken = link(owner=None)  # type: ignore[arg-type]  # NOT NULL violation: a bug, not "code taken"
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        repo.insert_link(broken)


def test_find_reusable_link_returns_latest_active_permanent(repo: SqliteRepository) -> None:
    repo.insert_link(link("old0001", created=T0))
    repo.insert_link(link("new0001", created=T0 + timedelta(minutes=1)))
    repo.insert_link(link("off0001", created=T0 + timedelta(minutes=2), active=False))
    repo.insert_link(link("exp0001", created=T0 + timedelta(minutes=3), expires=T0 + timedelta(days=1)))
    repo.insert_link(link("oth0001", owner="team-b", created=T0 + timedelta(minutes=4)))
    found = repo.find_reusable_link("team-a", "https://example.com/a")
    assert found is not None and found.code == "new0001"   # newest that is active, permanent and owned


def test_only_expiring_links_means_nothing_reusable(repo: SqliteRepository) -> None:
    """Regression guard: filtering must happen before LIMIT 1, or an expiring link hides the answer."""
    repo.insert_link(link("exp0001", expires=T0 + timedelta(days=1)))
    assert repo.find_reusable_link("team-a", "https://example.com/a") is None


def test_deactivate_is_soft_and_idempotent(repo: SqliteRepository) -> None:
    repo.insert_link(link())
    assert repo.deactivate("abc1234") is True
    assert repo.deactivate("abc1234") is False
    assert repo.deactivate("missing") is False
    stored = repo.get_link("abc1234")
    assert stored is not None and stored.is_active is False   # row kept, so the code is never reused


# ---------------- clicks
def test_record_click_counts_humans_not_bots(repo: SqliteRepository) -> None:
    repo.insert_link(link())
    repo.record_click(click(at=T0 + timedelta(seconds=2)))
    repo.record_click(click(bot=True, at=T0 + timedelta(seconds=1)))
    stored = repo.get_link("abc1234")
    assert stored is not None and stored.click_count == 1
    clicks = repo.clicks_for("abc1234")
    assert [c.is_bot for c in clicks] == [True, False]          # ordered by time
    assert clicks[1] == click(at=T0 + timedelta(seconds=2))


def test_click_for_unknown_link_rolls_back(repo: SqliteRepository) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        repo.record_click(click("missing"))
    assert repo.clicks_for("missing") == []


def test_counter_and_click_row_succeed_or_fail_together(repo: SqliteRepository) -> None:
    repo.insert_link(link())
    repo._conn.execute("CREATE TRIGGER block_counter BEFORE UPDATE OF click_count ON links "
                       "BEGIN SELECT RAISE(ABORT, 'counter unavailable'); END")
    with pytest.raises(sqlite3.IntegrityError, match="counter unavailable"):
        repo.record_click(click())
    assert repo.clicks_for("abc1234") == []                       # the click insert was rolled back too


# ---------------- ops
def test_ping(repo: SqliteRepository) -> None:
    assert repo.ping() is True
    repo.close()
    assert repo.ping() is False


def test_file_database_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    r = SqliteRepository(str(tmp_path / "links.db"))
    assert r._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert r._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    r.close()


# ---------------- schema versioning
V1_SCHEMA = """
CREATE TABLE links (code TEXT PRIMARY KEY, target_url TEXT NOT NULL, owner TEXT NOT NULL, created_at TEXT NOT NULL,
    expires_at TEXT, is_active INTEGER NOT NULL DEFAULT 1, click_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE clicks (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL REFERENCES links(code), ts TEXT NOT NULL,
    referrer_host TEXT, agent_family TEXT NOT NULL, is_bot INTEGER NOT NULL, ip_hash TEXT);
INSERT INTO links VALUES ('old0001', 'https://example.com/old', 'team-a', '2026-01-01T00:00:00+00:00', NULL, 1, 3);
INSERT INTO clicks(code, ts, agent_family, is_bot) VALUES ('old0001', '2026-01-01T00:00:00+00:00', 'chrome', 0);
"""


def test_database_from_previous_version_is_upgraded_in_place(tmp_path: Path) -> None:
    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.close()
    r = SqliteRepository(str(path))
    old = r.get_link("old0001")
    assert old is not None and old.click_count == 3 and old.stats_token_hash is None     # data kept
    assert r._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    r.record_click(click("old0001"))                                                     # new columns usable
    assert [c.ip_id for c in r.clicks_for("old0001")] == [None, -42]
    r.close()
    again = SqliteRepository(str(path))                                                  # idempotent reopen
    assert again.get_link("old0001") is not None
    again.close()


def test_schema_version_constants_agree() -> None:
    assert max(MIGRATIONS) == SCHEMA_VERSION and SET_SCHEMA_VERSION.endswith(f"= {SCHEMA_VERSION}")


def test_fresh_database_is_at_current_version(repo: SqliteRepository) -> None:
    assert repo._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_stats_token_hash_round_trip(repo: SqliteRepository) -> None:
    repo.insert_link(Link(code="tok0001", target_url="https://example.com/t", owner="o", created_at=T0,
                          expires_at=None, is_active=True, click_count=0, stats_token_hash="ab" * 32))
    stored = repo.get_link("tok0001")
    assert stored is not None and stored.stats_token_hash == "ab" * 32


def test_all_codes(repo: SqliteRepository) -> None:
    repo.insert_link(Link(code="aaa0001", target_url="https://example.com/a", owner="o", created_at=T0,
                          expires_at=None, is_active=True, click_count=0))
    assert repo.all_codes() == ["aaa0001"]
