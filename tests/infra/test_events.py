"""Event pipeline on real servers: Redis Streams bus, Postgres outbox, worker commands."""
import json
import runpy
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from redis import Redis

from urlshort.adapters import workers
from urlshort.adapters.postgres import PostgresRepository
from urlshort.adapters.redis_events import RedisStreamBus
from urlshort.api import create_app
from urlshort.config import ConfigError, Settings
from urlshort.events import Aggregator, Message, Relay, drain, encode_click
from urlshort.storage import SqliteRepository

CHROME = {"user-agent": "Mozilla/5.0 Chrome/126.0"}


def url_of(client: Redis) -> str:
    return f"redis://localhost:{client.connection_pool.connection_kwargs['port']}/0"


def test_stream_publish_read_ack(redis_client: Redis) -> None:
    bus = RedisStreamBus(redis_client)
    RedisStreamBus(redis_client)                                  # creating the group twice is fine
    bus.publish(["a", "b", "c"])
    first = bus.read("c1", 2)
    assert [m.payload for m in first] == ["a", "b"] and all(m.deliveries == 1 for m in first)
    assert bus.lag() == 1
    bus.ack([m.id for m in first])
    assert [m.payload for m in bus.read("c1", 10)] == ["c"] and bus.lag() == 0


def test_messages_from_a_crashed_consumer_are_reclaimed(redis_client: Redis) -> None:
    bus = RedisStreamBus(redis_client, claim_idle_ms=0)           # reclaim immediately for the test
    bus.publish(["x"])
    assert [m.deliveries for m in bus.read("crashed", 1)] == [1]  # read, never acknowledged
    reclaimed = bus.read("healthy", 1)
    assert [(m.payload, m.deliveries) for m in reclaimed] == [("x", 2)]


def test_dead_letters_and_replay(redis_client: Redis) -> None:
    bus = RedisStreamBus(redis_client)
    bus.dead_letter(Message("1-0", "{bad", 3), "not JSON")
    (entry,) = redis_client.xrange(bus.dlq)
    assert entry[1][b"reason"] == b"not JSON" and entry[1][b"original_id"] == b"1-0"
    bus.publish(["p"])
    bus.ack([m.id for m in bus.read("c", 10)])
    bus.rewind()
    assert [m.payload for m in bus.read("c", 10)] == ["p"]       # replay from the start


def test_unexpected_group_errors_are_not_swallowed(redis_client: Redis) -> None:
    redis_client.set("not-a-stream", "x")
    with pytest.raises(Exception, match="WRONGTYPE"):
        RedisStreamBus(redis_client, stream="not-a-stream")


def test_end_to_end_on_postgres_and_redis(pg: PostgresRepository, redis_client: Redis) -> None:
    """Redirects -> Postgres outbox (same transaction as the count) -> relay -> Redis Stream -> aggregator."""
    client = TestClient(create_app(Settings(analytics_mode="events"), pg))
    made = client.post("/api/v1/links", json={"url": "https://example.com/ev", "max_clicks": 5}).json()
    for _ in range(3):
        assert client.get(f"/{made['code']}", headers=CHROME, follow_redirects=False).status_code == 307
    assert pg.outbox_backlog() == 3 and pg.clicks_for(made["code"]) == []
    bus = RedisStreamBus(redis_client)
    relay, agg = Relay(pg, bus), Aggregator(pg, bus)
    assert drain(relay, agg)["applied"] == 3 and pg.outbox_backlog() == 0
    stats = client.get(f"/api/v1/links/{made['code']}/stats", headers={"x-stats-token": made["stats_token"]})
    assert stats.json()["total_clicks"] == 3
    bus.publish([encode_click(pg.clicks_for(made["code"])[0])])   # a duplicate delivery
    bus.rewind()
    assert drain(relay, agg)["duplicates"] >= 3 and len(pg.clicks_for(made["code"])) == 3


def test_worker_commands(redis_client: Redis, capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(redis_url=url_of(redis_client), db_path=":memory:")
    assert workers.main(["drain"], settings=settings) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] == 0 and out["lag"] == 0 and out["outbox_backlog"] == 0
    assert workers.main(["replay"], settings=settings) == 0
    stop = threading.Event()
    calls: list[int] = []

    def step() -> int:
        calls.append(1)
        if len(calls) == 2:
            stop.set()
        return 1 if len(calls) == 1 else 0
    workers.loop(step, stop, idle_sleep=0)
    assert len(calls) == 2
    for command in ("relay", "aggregate"):
        stopped = threading.Event()
        stopped.set()                                              # already asked to stop: returns at once
        assert workers.main([command], settings=settings, stop=stopped) == 0


def test_workers_need_redis_and_use_postgres_when_configured(redis_client: Redis, pg_url: str) -> None:
    with pytest.raises(ConfigError, match="URLSHORT_REDIS_URL"):
        workers.components(Settings())
    repo, *_ = workers.components(Settings(redis_url=url_of(redis_client), database_url=pg_url))
    assert isinstance(repo, PostgresRepository)
    repo.close()
    sqlite_repo, *_ = workers.components(Settings(redis_url=url_of(redis_client)))
    assert isinstance(sqlite_repo, SqliteRepository)


def test_module_entry_point(redis_client: Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("URLSHORT_REDIS_URL", url_of(redis_client))
    monkeypatch.setattr(sys, "argv", ["workers", "drain"])
    monkeypatch.delitem(sys.modules, "urlshort.adapters.workers", raising=False)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("urlshort.adapters.workers", run_name="__main__")
    assert exc.value.code == 0
