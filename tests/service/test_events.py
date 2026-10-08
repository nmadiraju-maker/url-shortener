"""Event-driven analytics with the in-memory bus: outbox, relay, idempotent aggregator, DLQ, replay."""
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from urlshort.api import create_app
from urlshort.config import ConfigError, Settings, load_settings
from urlshort.events import (
    Aggregator,
    InMemoryBus,
    InvalidEvent,
    Message,
    Relay,
    decode_click,
    drain,
    encode_click,
    new_event_id,
)
from urlshort.service import ShortenerService
from urlshort.storage import SCHEMA_VERSION, Click, SqliteRepository

from .helpers import FakeClock

CHROME = {"user-agent": "Mozilla/5.0 Chrome/126.0"}
EVENTS = Settings(analytics_mode="events")


def a_click(code: str = "c", **kw: object) -> Click:
    base: dict[str, object] = {"code": code, "ts": FakeClock()(), "referrer_host": "news.example",
                               "agent_family": "chrome", "is_bot": False, "ip_id": 7, "ip_key_id": "2026-01-15",
                               "event_id": new_event_id()}
    return Click(**{**base, **kw})  # type: ignore[arg-type]


# ---------------------------------------------------------------- event schema
def test_click_events_round_trip() -> None:
    click = a_click()
    assert decode_click(encode_click(click)) == click and json.loads(encode_click(click))["type"] == "click.v1"


@pytest.mark.parametrize("payload,reason", [
    ("{not json", "not JSON"), ("[1, 2]", "unknown event type"), ('{"type": "order.v1"}', "unknown event type"),
    ('{"type": "click.v1", "ts": "2026-01-15T12:00:00+00:00"}', "malformed"),
    ('{"type": "click.v1", "ts": "yesterday", "event_id": "e"}', "malformed"),
    (json.dumps({**json.loads(encode_click(a_click())), "is_bot": "no"}), "mistyped"),
    (json.dumps({**json.loads(encode_click(a_click())), "event_id": ""}), "mistyped"),
])
def test_malformed_events_are_rejected(payload: str, reason: str) -> None:
    with pytest.raises(InvalidEvent, match=reason):
        decode_click(payload)


# ---------------------------------------------------------------- outbox
def test_events_mode_writes_the_outbox_atomically_with_the_counter(repo: SqliteRepository, clock: FakeClock) -> None:
    svc = ShortenerService(repo, EVENTS, clock=clock)
    code = svc.shorten("https://example.com/e", owner="o").link.code
    capped = svc.shorten("https://example.com/cap", owner="o", max_clicks=1).link.code
    svc.resolve(code, user_agent=CHROME["user-agent"])
    svc.resolve(capped, user_agent=CHROME["user-agent"])
    assert repo.clicks_for(code) == [] and repo.outbox_backlog() == 2            # details wait for the workers
    link = repo.get_link(code)
    assert link is not None and link.click_count == 1                           # counts stay synchronous
    repo._conn.execute("DROP TABLE click_outbox")
    with pytest.raises(sqlite3.OperationalError):
        repo.record_click(a_click(code), as_event=True)
    after = repo.get_link(code)
    assert after is not None and after.click_count == 1                         # no count without its event


def test_relay_publishes_then_marks(repo: SqliteRepository, clock: FakeClock) -> None:
    svc, bus = ShortenerService(repo, EVENTS, clock=clock), InMemoryBus()
    code = svc.shorten("https://example.com/r", owner="o").link.code
    for _ in range(3):
        svc.resolve(code)
    relay = Relay(repo, bus, clock=clock, batch=2)
    assert (relay.run_once(), relay.run_once(), relay.run_once()) == (2, 1, 0)
    assert len(bus.entries) == 3 and repo.outbox_backlog() == 0


# ---------------------------------------------------------------- aggregator
def test_duplicates_are_applied_once(repo: SqliteRepository, clock: FakeClock) -> None:
    svc, bus = ShortenerService(repo, EVENTS, clock=clock), InMemoryBus()
    code = svc.shorten("https://example.com/d", owner="o").link.code
    svc.resolve(code)
    payload = repo.outbox_pending(1)[0][1]
    bus.publish([payload, payload])                                   # e.g. a relay crashed after publishing
    stats = drain(Relay(repo, bus), Aggregator(repo, bus))
    assert stats["applied"] == 1 and stats["duplicates"] == 2 and len(repo.clicks_for(code)) == 1


def test_malformed_events_go_straight_to_the_dead_letter_queue(repo: SqliteRepository) -> None:
    bus = InMemoryBus()
    bus.publish(["{broken"])
    agg = Aggregator(repo, bus)
    assert agg.run_once() == 1 and agg.stats["dead"] == 1 and bus.pending == {}
    assert "not JSON" in bus.dead[0][1]


def test_failing_events_are_retried_then_dead_lettered(repo: SqliteRepository, clock: FakeClock) -> None:
    bus = InMemoryBus()
    bus.publish([encode_click(a_click("nolink"))])                   # FK violation every time
    agg = Aggregator(repo, bus, max_deliveries=3)
    for _ in range(3):
        agg.run_once()
    assert agg.stats["failed"] == 2 and agg.stats["dead"] == 1 and bus.pending == {}
    assert "failed 3 times" in bus.dead[0][1]


def test_replay_is_safe_and_stats_are_correct(repo: SqliteRepository, clock: FakeClock) -> None:
    client = TestClient(create_app(EVENTS, repo, clock=clock))
    made = client.post("/api/v1/links", json={"url": "https://example.com/s"}).json()
    for _ in range(2):
        client.get(f"/{made['code']}", headers=CHROME, follow_redirects=False)
    stats_url, token = f"/api/v1/links/{made['code']}/stats", {"x-stats-token": made["stats_token"]}
    assert client.get(stats_url, headers=token).json()["total_clicks"] == 0     # not yet aggregated
    bus = InMemoryBus()
    relay, agg = Relay(repo, bus), Aggregator(repo, bus)
    drain(relay, agg)
    assert client.get(stats_url, headers=token).json()["total_clicks"] == 2
    bus.rewind()
    assert drain(relay, agg)["duplicates"] == 2                                # replayed, nothing double-counted
    assert client.get(stats_url, headers=token).json()["total_clicks"] == 2


def test_in_memory_bus_redelivers_until_acknowledged() -> None:
    bus = InMemoryBus()
    bus.publish(["a", "b"])
    assert [m.deliveries for m in bus.read("c", 10)] == [1, 1]
    assert [(m.payload, m.deliveries) for m in bus.read("c", 1)] == [("a", 2)]
    bus.ack(["1-0", "2-0"])
    assert bus.read("c", 10) == [] and isinstance(Message("1-0", "a", 1), Message)


# ---------------------------------------------------------------- schema and config
def test_version_3_database_gains_the_outbox(tmp_path: Path) -> None:
    path = tmp_path / "v3.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE links (code TEXT PRIMARY KEY, target_url TEXT NOT NULL, owner TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT, is_active INTEGER NOT NULL DEFAULT 1,
            click_count INTEGER NOT NULL DEFAULT 0, stats_token_hash TEXT, max_clicks INTEGER);
        CREATE TABLE clicks (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, ts TEXT NOT NULL,
            referrer_host TEXT, agent_family TEXT NOT NULL, is_bot INTEGER NOT NULL, ip_id INTEGER, ip_key_id TEXT);
        INSERT INTO clicks(code, ts, agent_family, is_bot) VALUES ('old', '2026-01-01T00:00:00+00:00', 'chrome', 0);
        PRAGMA user_version = 3;
    """)
    conn.close()
    r = SqliteRepository(str(path))
    assert r._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION >= 4   # current, whatever it is
    assert [c.event_id for c in r.clicks_for("old")] == [None] and r.outbox_backlog() == 0
    r.close()


def test_analytics_mode_setting(tmp_path: Path) -> None:
    assert load_settings(None, {"URLSHORT_ANALYTICS_MODE": "events"}).analytics_mode == "events"
    path = tmp_path / "c.toml"
    path.write_text('[analytics]\nmode = "events"\n')
    assert load_settings(path, {}).analytics_mode == "events"
    with pytest.raises(ConfigError, match="analytics mode"):
        load_settings(None, {"URLSHORT_ANALYTICS_MODE": "kafka"})


def test_drain_is_bounded(repo: SqliteRepository, clock: FakeClock) -> None:
    """A one-shot drain must stop after max_rounds even if work remains (it never spins forever)."""
    svc, bus = ShortenerService(repo, EVENTS, clock=clock), InMemoryBus()
    code = svc.shorten("https://example.com/b", owner="o").link.code
    for _ in range(3):
        svc.resolve(code)
    drain(Relay(repo, bus, batch=1), Aggregator(repo, bus, batch=1), max_rounds=1)
    assert repo.outbox_backlog() == 2 and len(repo.clicks_for(code)) == 1
