"""Redis adapters on a real server: shared rate limits, link cache with invalidation, Bloom filter."""
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from redis import Redis

from urlshort.adapters.redis_cache import BloomFilter, CachedRepository
from urlshort.adapters.redis_ratelimit import RedisGcraLimiter
from urlshort.adapters.wiring import build
from urlshort.api import create_app
from urlshort.config import Settings
from urlshort.storage import Link, SqliteRepository

T0 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
DOWN = "redis://localhost:1/0"          # nothing listens here: every call fails like an outage


def link(code: str) -> Link:
    return Link(code=code, target_url="https://example.com/x", owner="o", created_at=T0, expires_at=None,
                is_active=True, click_count=0)


# ---------------------------------------------------------------- rate limiting
def test_gcra_in_redis_matches_the_in_memory_semantics(redis_client: Redis) -> None:
    lim = RedisGcraLimiter(redis_client, "t", rate_per_minute=1, burst=3)
    results = [lim.acquire("client-a") for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results] == [2, 1, 0, 0]
    assert 59 <= results[3].retry_after <= 60 and results[3].headers()["Retry-After"] == "60"
    for _ in range(20):                                         # refusals do not push the next slot further
        lim.acquire("client-a")
    assert 59 <= lim.acquire("client-a").retry_after <= 60
    assert lim.acquire("client-b").allowed                      # clients are independent
    assert 0 < redis_client.pttl("urlshort:rl:t:client-a") <= 180_000   # state expires on its own


def test_limit_is_shared_between_instances(redis_client: Redis) -> None:
    a = RedisGcraLimiter(redis_client, "shared", rate_per_minute=1, burst=2)
    b = RedisGcraLimiter(Redis.from_url(redis_client.connection_pool.connection_kwargs and
                                        f"redis://localhost:{redis_client.connection_pool.connection_kwargs['port']}"
                                        f"/{redis_client.connection_pool.connection_kwargs.get('db', 0)}"),
                         "shared", rate_per_minute=1, burst=2)
    assert a.acquire("c").allowed and b.acquire("c").allowed and not a.acquire("c").allowed


def test_limiter_fails_open_when_redis_is_down(caplog: pytest.LogCaptureFixture) -> None:
    lim = RedisGcraLimiter(Redis.from_url(DOWN, socket_connect_timeout=0.2), "x", 60, 2)
    decision = lim.acquire("c")
    assert decision.allowed and decision.remaining == 2 and "rate limiter unavailable" in caplog.text


def test_limiter_parameters_are_validated(redis_client: Redis) -> None:
    with pytest.raises(ValueError):
        RedisGcraLimiter(redis_client, "x", 0, 1)


# ---------------------------------------------------------------- cache and Bloom filter
def test_cache_serves_hits_and_caches_misses(redis_client: Redis) -> None:
    db = SqliteRepository()
    cache = CachedRepository(db, redis_client, ttl=60, negative_ttl=30)
    db.insert_link(link("abc1234"))
    assert cache.get_link("abc1234") is not None
    db._conn.execute("UPDATE links SET target_url = 'https://changed.example' WHERE code = 'abc1234'")
    cached = cache.get_link("abc1234")
    assert cached is not None and cached.target_url == "https://example.com/x"     # served from Redis
    assert cache.get_link("missing") is None and redis_client.get("urlshort:link:missing") == b"-"
    db.insert_link(link("missing"))
    assert cache.get_link("missing") is None                     # negative entry still fresh
    cache.insert_link(link("newcode"))
    assert redis_client.get("urlshort:link:newcode") is None and cache.get_link("newcode") is not None


def test_takedown_clears_the_cache_everywhere(redis_client: Redis) -> None:
    db = SqliteRepository()
    a, b = CachedRepository(db, redis_client), CachedRepository(db, redis_client)
    a.insert_link(link("bad0001"))
    got = b.get_link("bad0001")
    assert got is not None and got.is_active
    assert a.deactivate("bad0001") is True
    after = b.get_link("bad0001")
    assert after is not None and after.is_active is False       # instance B sees the takedown at once


def test_bloom_filter_rules_out_unknown_codes_only_once_built(redis_client: Redis) -> None:
    db = SqliteRepository()
    db.insert_link(link("old0001"))
    bloom = BloomFilter(redis_client, bits=1 << 16)
    cache = CachedRepository(db, redis_client, bloom=bloom)
    assert bloom.might_contain("anything")                       # not built: cannot rule anything out
    assert bloom.build(db.all_codes) is True and bloom.build(db.all_codes) is False   # built once
    assert bloom.might_contain("old0001") and not bloom.might_contain("zzzzzzz")
    queries: list[str] = []
    db.get_link = lambda code: queries.append(code)  # type: ignore[assignment,method-assign,return-value]
    assert cache.get_link("zzzzzzz") is None and queries == []  # no database query for a scanned code
    cache.insert_link(link("new0001"))
    assert bloom.might_contain("new0001")


def test_cache_falls_back_to_the_database_when_redis_is_down(caplog: pytest.LogCaptureFixture) -> None:
    db = SqliteRepository()
    down = Redis.from_url(DOWN, socket_connect_timeout=0.2)
    cache = CachedRepository(db, down, bloom=BloomFilter(down))
    cache.insert_link(link("ok00001"))
    assert cache.get_link("ok00001") is not None and cache.deactivate("ok00001") is True
    assert "cache unavailable" in caplog.text


def test_pass_through_methods_reach_the_database(redis_client: Redis) -> None:
    from sdlc.workspace import Workspace  # noqa: F401  (import check only: adapters do not depend on sdlc)
    db = SqliteRepository()
    cache = CachedRepository(db, redis_client)
    cache.insert_link(link("pas0001"))
    from urlshort.storage import Click
    c = Click(code="pas0001", ts=T0, referrer_host=None, agent_family="chrome", is_bot=False, ip_id=None,
              ip_key_id=None)
    cache.record_click(c)
    assert cache.record_click_with_limit(c) is True and len(cache.clicks_for("pas0001")) == 2
    assert cache.find_reusable_link("o", "https://example.com/x") is not None and cache.all_codes() == ["pas0001"]
    assert cache.append_audit("t", "a", "x", "y", "{}", lambda prev: ("0" * 64, "1" * 64)) == "1" * 64
    assert len(cache.audit_records()) == 1 and cache.ping() is True


# ---------------------------------------------------------------- wiring and multi-instance behaviour
def test_two_app_instances_share_limits_and_takedowns(redis_client: Redis, pg_url: str) -> None:
    from urlshort.adapters.postgres import PostgresRepository
    url = f"redis://localhost:{redis_client.connection_pool.connection_kwargs['port']}/0"
    settings = Settings(database_url=pg_url, redis_url=url, admin_api_key="k", create_burst=2)
    pg = PostgresRepository(pg_url)
    with pg._pool.connection() as conn:
        conn.execute("TRUNCATE links, clicks, audit_log RESTART IDENTITY CASCADE")
    one, two = TestClient(create_app(settings)), TestClient(create_app(settings))
    code = one.post("/api/v1/links", json={"url": "https://example.com/a"}).json()["code"]
    assert two.post("/api/v1/links", json={"url": "https://example.com/b"}).status_code == 201
    assert one.post("/api/v1/links", json={"url": "https://example.com/c"}).status_code == 429   # shared allowance
    assert two.get(f"/{code}", follow_redirects=False).status_code == 307                      # cached on two
    assert one.delete(f"/api/v1/links/{code}", headers={"x-api-key": "k"}).status_code == 204
    assert two.get(f"/{code}", follow_redirects=False).status_code == 404                      # takedown applies
    assert two.get("/zzzzzzz", follow_redirects=False).status_code == 404                      # Bloom: no DB hit
    pg.close()


def test_wiring_variants(redis_client: Redis, pg_url: str, caplog: pytest.LogCaptureFixture) -> None:
    from urlshort.adapters.postgres import PostgresRepository
    repo, limiters = build(Settings(database_url=pg_url), None)
    assert isinstance(repo, PostgresRepository) and limiters is None
    repo.close()
    sqlite = SqliteRepository()
    repo2, limiters2 = build(Settings(redis_url=DOWN), sqlite)
    assert isinstance(repo2, CachedRepository) and limiters2 is not None
    assert "Bloom filter not built" in caplog.text
    url = f"redis://localhost:{redis_client.connection_pool.connection_kwargs['port']}/0"
    repo3, _ = build(Settings(redis_url=url), None)
    assert isinstance(repo3, CachedRepository)
