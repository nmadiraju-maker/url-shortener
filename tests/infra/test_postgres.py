"""The PostgreSQL adapter honours the same Repository contract as SQLite, on a real server."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import psycopg
import pytest
from fastapi.testclient import TestClient

from urlshort.adapters.postgres import PostgresRepository, migrate
from urlshort.api import create_app
from urlshort.audit import AuditTrail
from urlshort.config import Settings
from urlshort.errors import AliasConflict, LinkExhausted
from urlshort.service import ShortenerService
from urlshort.storage import SCHEMA_VERSION, Click, Link

T0 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def link(code: str, **kw: object) -> Link:
    base: dict[str, object] = {"code": code, "target_url": "https://example.com/x", "owner": "o", "created_at": T0,
                               "expires_at": None, "is_active": True, "click_count": 0}
    return Link(**{**base, **kw})  # type: ignore[arg-type]


def click(code: str, *, bot: bool = False, at: datetime = T0) -> Click:
    return Click(code=code, ts=at, referrer_host="news.example", agent_family="bot" if bot else "chrome",
                 is_bot=bot, ip_id=-42, ip_key_id="2026-01-15")


def test_round_trip_keeps_types_and_timezones(pg: PostgresRepository) -> None:
    pg.insert_link(link("abc1234", expires_at=T0 + timedelta(days=1), stats_token_hash="ab" * 32, max_clicks=5))
    got = pg.get_link("abc1234")
    assert got is not None and got.expires_at == T0 + timedelta(days=1) and got.created_at.tzinfo is not None
    assert (got.stats_token_hash, got.max_clicks, got.is_active) == ("ab" * 32, 5, True)
    assert pg.get_link("nope") is None and pg.all_codes() == ["abc1234"]


def test_only_a_duplicate_code_is_an_alias_conflict(pg: PostgresRepository) -> None:
    pg.insert_link(link("dup0001"))
    with pytest.raises(AliasConflict):
        pg.insert_link(link("dup0001"))
    with pg._pool.connection() as conn:
        conn.execute("CREATE UNIQUE INDEX ux_test_target ON links(target_url)")
    try:
        with pytest.raises(psycopg.errors.UniqueViolation):           # another constraint: not a 409
            pg.insert_link(link("dup0002"))
    finally:
        with pg._pool.connection() as conn:
            conn.execute("DROP INDEX ux_test_target")


def test_reuse_only_plain_active_permanent_links(pg: PostgresRepository) -> None:
    pg.insert_link(link("exp0001", expires_at=T0 + timedelta(days=1)))
    pg.insert_link(link("cap0001", max_clicks=3, created_at=T0 + timedelta(seconds=1)))
    assert pg.find_reusable_link("o", "https://example.com/x") is None
    pg.insert_link(link("plain01", created_at=T0 + timedelta(seconds=2)))
    found = pg.find_reusable_link("o", "https://example.com/x")
    assert found is not None and found.code == "plain01"
    assert pg.deactivate("plain01") is True and pg.deactivate("plain01") is False
    assert pg.find_reusable_link("o", "https://example.com/x") is None


def test_clicks_count_humans_and_roll_back_atomically(pg: PostgresRepository) -> None:
    pg.insert_link(link("clk0001"))
    pg.record_click(click("clk0001"))
    pg.record_click(click("clk0001", bot=True, at=T0 + timedelta(seconds=1)))
    got = pg.get_link("clk0001")
    assert got is not None and got.click_count == 1
    assert [c.is_bot for c in pg.clicks_for("clk0001")] == [False, True]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        pg.record_click(click("missing"))                              # no row, no counter change


def test_click_cap_is_exact_under_concurrency(pg: PostgresRepository) -> None:
    """AC-MAXCLICKS-1 on Postgres: 40 simultaneous redirects on a 10-click link -> exactly 10."""
    svc = ShortenerService(pg, Settings())
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
    got = pg.get_link(code)
    assert got is not None and got.click_count == 10 and len(pg.clicks_for(code)) == 10


def test_bots_are_checked_against_but_do_not_consume_the_cap(pg: PostgresRepository) -> None:
    pg.insert_link(link("bot0001", max_clicks=1))
    assert pg.record_click_with_limit(click("bot0001", bot=True)) is True
    assert pg.record_click_with_limit(click("bot0001")) is True
    assert pg.record_click_with_limit(click("bot0001", bot=True)) is False
    assert pg.record_click_with_limit(click("bot0001")) is False


def test_audit_chain_never_forks_with_concurrent_writers(pg: PostgresRepository) -> None:
    trails = [AuditTrail(pg), AuditTrail(pg)]

    def write(i: int) -> None:
        trails[i % 2].record(when=T0, actor="a", action="link.create", target=f"c{i}", details={"i": i})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(200)))
    records = pg.audit_records()
    assert len(records) == 200 and AuditTrail(pg).verify()
    assert len({r.prev_hash for r in records}) == 200                  # every record has a distinct parent


def test_service_and_api_work_end_to_end_on_postgres(pg: PostgresRepository) -> None:
    client = TestClient(create_app(Settings(admin_api_key="k"), pg))
    made = client.post("/api/v1/links", json={"url": "https://example.com/pg", "max_clicks": 2}).json()
    assert client.get(f"/{made['code']}", follow_redirects=False).status_code == 307
    stats = client.get(f"/api/v1/links/{made['code']}/stats", headers={"x-stats-token": made["stats_token"]}).json()
    assert stats["total_clicks"] == 1 and stats["bot_clicks"] == 0
    assert client.get("/readyz").json() == {"status": "ready"}
    assert client.delete(f"/api/v1/links/{made['code']}", headers={"x-api-key": "k"}).status_code == 204


def test_migrations_are_idempotent_and_match_the_sqlite_schema_version(pg_url: str) -> None:
    assert migrate(pg_url).startswith(f"v{SCHEMA_VERSION}_")           # parity with SQLite's version
    assert migrate(pg_url).startswith(f"v{SCHEMA_VERSION}_")
    repo = PostgresRepository(pg_url, auto_migrate=False)
    assert repo.ping() is True
    repo.close()
    assert repo.ping() is False                                       # closed pool: not ready, no exception


def test_every_migration_is_reversible(pg_url: str) -> None:
    """Down to an empty database and back up: each revision's downgrade undoes its upgrade."""
    from alembic import command

    from urlshort.adapters.postgres import alembic_config

    def tables() -> set[str]:
        with psycopg.connect(pg_url) as conn:
            rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
        return {r[0] for r in rows} - {"alembic_version"}

    def columns() -> set[str]:
        with psycopg.connect(pg_url) as conn:
            rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'links'")
            return {r[0] for r in rows.fetchall()}
    cfg = alembic_config(pg_url)
    command.downgrade(cfg, "v2_baseline")
    assert "max_clicks" not in columns()
    command.downgrade(cfg, "base")
    assert tables() == set()
    assert migrate(pg_url).startswith(f"v{SCHEMA_VERSION}_")
    assert tables() == {"links", "clicks", "audit_log", "click_outbox"} and "max_clicks" in columns()
