"""Event workers:  python -m urlshort.adapters.workers {relay|aggregate|drain|replay}

  relay      publish outbox events to the stream (loop)
  aggregate  apply events to the clicks table (loop; run several with different --consumer names)
  drain      run both until idle, then exit (one-shot; handy for jobs and tests)
  replay     rewind the consumer group to the start of the stream, then drain (safe: apply is idempotent)

Needs URLSHORT_REDIS_URL, and the same storage settings as the API (URLSHORT_DATABASE_URL or URLSHORT_DB_PATH).
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections.abc import Callable

from redis import Redis

from ..config import ConfigError, Settings
from ..events import Aggregator, Relay, drain
from ..logging_setup import configure_logging
from ..storage import Repository, SqliteRepository
from .redis_events import RedisStreamBus


def components(settings: Settings, *, consumer: str = "aggregator-1") -> tuple[Repository, RedisStreamBus, Relay,
                                                                                    Aggregator]:
    if not settings.redis_url:
        raise ConfigError("event workers need URLSHORT_REDIS_URL")
    repo: Repository
    if settings.database_url:
        from .postgres import PostgresRepository
        repo = PostgresRepository(settings.database_url)
    else:
        repo = SqliteRepository(settings.db_path)
    bus = RedisStreamBus(Redis.from_url(settings.redis_url))
    return repo, bus, Relay(repo, bus), Aggregator(repo, bus, consumer=consumer)


def loop(step: Callable[[], int], stop: threading.Event, idle_sleep: float = 0.5) -> None:
    """Call `step` until `stop` is set, sleeping only when there was nothing to do."""
    while not stop.is_set():
        if step() == 0:
            stop.wait(idle_sleep)


def main(argv: list[str] | None = None, *, settings: Settings | None = None,
         stop: threading.Event | None = None, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(prog="urlshort.adapters.workers")
    parser.add_argument("command", choices=["relay", "aggregate", "drain", "replay"])
    parser.add_argument("--consumer", default="aggregator-1")
    args = parser.parse_args(argv)
    configure_logging()
    repo, bus, relay, aggregator = components(settings or Settings.from_env(), consumer=args.consumer)
    stop = stop or threading.Event()
    if args.command == "relay":
        loop(relay.run_once, stop)
    elif args.command == "aggregate":
        loop(aggregator.run_once, stop)
    else:
        if args.command == "replay":
            bus.rewind()
        started = time.monotonic()
        result = drain(relay, aggregator)
        out(json.dumps({**result, "seconds": round(time.monotonic() - started, 3), "lag": bus.lag(),
                          "outbox_backlog": repo.outbox_backlog()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
