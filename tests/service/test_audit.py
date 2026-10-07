from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from urlshort.audit import GENESIS, AuditTrail, digest
from urlshort.storage import SqliteRepository

T0 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo() -> Iterator[SqliteRepository]:
    r = SqliteRepository()
    yield r
    r.close()


def test_records_are_hash_chained(repo: SqliteRepository) -> None:
    trail = AuditTrail(repo)
    h1 = trail.record(when=T0, actor="team-a", action="link.create", target="abc1234", details={"url": "x"})
    h2 = trail.record(when=T0, actor="admin", action="link.deactivate", target="abc1234", details={})
    first, second = repo.audit_records()
    assert (first.prev_hash, first.hash, second.prev_hash, second.hash) == (GENESIS, h1, h1, h2)
    assert first.hash == digest(GENESIS, first.ts, "team-a", "link.create", "abc1234", '{"url": "x"}')
    assert trail.verify() is True


def test_empty_trail_verifies(repo: SqliteRepository) -> None:
    assert AuditTrail(repo).verify() is True


def test_timestamps_normalised_to_utc_and_naive_refused(repo: SqliteRepository) -> None:
    trail = AuditTrail(repo)
    trail.record(when=datetime(2026, 1, 15, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
                 actor="a", action="x", target="t", details={})
    assert repo.audit_records()[0].ts == "2026-01-15T12:00:00+00:00"
    with pytest.raises(ValueError, match="timezone-aware"):
        trail.record(when=datetime(2026, 1, 15), actor="a", action="x", target="t", details={})


@pytest.mark.parametrize("tamper", [
    "UPDATE audit_log SET details = '{\"url\": \"evil\"}' WHERE id = 1",   # edited content
    "UPDATE audit_log SET actor = 'someone-else' WHERE id = 2",           # edited actor
    "DELETE FROM audit_log WHERE id = 2",                                 # deleted record
    "UPDATE audit_log SET prev_hash = 'f' WHERE id = 3",                  # broken link
])
def test_tampering_is_detected(repo: SqliteRepository, tamper: str) -> None:
    trail = AuditTrail(repo)
    for n in range(3):
        trail.record(when=T0, actor="team-a", action="link.create", target=f"c{n}", details={"url": "x"})
    repo._conn.execute(tamper)
    assert trail.verify() is False


def test_failed_append_leaves_no_partial_record(repo: SqliteRepository) -> None:
    def broken_chain(prev: str | None) -> tuple[str, str]:
        raise RuntimeError("hashing failed")
    with pytest.raises(RuntimeError):
        repo.append_audit("ts", "a", "x", "t", "{}", broken_chain)
    assert repo.audit_records() == []
    assert AuditTrail(repo).record(when=T0, actor="a", action="x", target="t", details={})  # lock released


def test_concurrent_writers_on_separate_connections_never_fork(tmp_path: Path) -> None:
    """Two connections act like two server processes. Each append must chain onto the true latest
    record; a read-then-write in separate steps would let both chain onto the same parent."""
    path = str(tmp_path / "audit.db")
    repos = [SqliteRepository(path), SqliteRepository(path)]
    trails = [AuditTrail(r) for r in repos]
    barrier = Barrier(8)

    def write(n: int) -> None:
        barrier.wait()
        for i in range(25):
            trails[n % 2].record(when=T0, actor=f"worker-{n}", action="link.create", target=f"{n}-{i}", details={})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))
    records = repos[0].audit_records()
    assert len(records) == 200
    assert len({r.prev_hash for r in records}) == 200          # every parent used exactly once: no fork
    assert trails[1].verify() is True
    for r in repos:
        r.close()
