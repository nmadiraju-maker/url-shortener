"""Builds the infrastructure-backed components when URLSHORT_DATABASE_URL and/or URLSHORT_REDIS_URL are set."""

from __future__ import annotations

import logging

from redis import Redis
from redis.exceptions import RedisError

from ..config import Settings
from ..ratelimit import RateLimiter
from ..storage import Repository, SqliteRepository
from .redis_cache import BloomFilter, CachedRepository
from .redis_ratelimit import RedisGcraLimiter

log = logging.getLogger("urlshort.api")


def build(settings: Settings, repo: Repository | None) -> tuple[Repository, tuple[RateLimiter, RateLimiter] | None]:
    if repo is None and settings.database_url:
        from .postgres import PostgresRepository
        repo = PostgresRepository(settings.database_url)
    repository: Repository = repo or SqliteRepository(settings.db_path)
    if not settings.redis_url:
        log.info("storage: postgres; rate limits: per instance (no Redis configured)")
        return repository, None
    client = Redis.from_url(settings.redis_url, socket_timeout=1.0, socket_connect_timeout=1.0)
    bloom = BloomFilter(client)
    try:
        bloom.build(repository.all_codes)
    except RedisError:
        log.warning("Bloom filter not built: Redis unavailable; lookups go to the cache/database", exc_info=True)
    cached = CachedRepository(repository, client, ttl=settings.cache_ttl_seconds, bloom=bloom)
    limiters = (RedisGcraLimiter(client, "create", settings.create_rate_per_minute, settings.create_burst),
                RedisGcraLimiter(client, "redirect", settings.redirect_rate_per_minute, settings.redirect_burst))
    log.info("storage with Redis cache and Bloom filter; rate limits shared across instances via Redis")
    return cached, limiters
