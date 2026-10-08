# Infrastructure adapters: PostgreSQL and Redis

Optional. Without them the service runs on SQLite with per-instance rate limits, exactly as before. Set
the URLs (environment only; they may contain passwords and are shown as `set`/`NOT SET` in the startup
summary):

```bash
URLSHORT_DATABASE_URL=postgresql://user:pass@db:5432/urlshort   # storage in Postgres
URLSHORT_REDIS_URL=redis://cache:6379/0                         # cache, Bloom filter, shared rate limits
URLSHORT_CACHE_TTL=60                                           # or [cache] ttl_seconds
```

Locally: `docker compose up -d` (Postgres 16, Redis 7), then `make ci-infra` runs the adapter tests against
them. CI runs the same tests in its **Infrastructure** job with real service containers.

## What each adapter guarantees

| Concern | How | Verified by |
|---|---|---|
| Click caps under concurrency (Postgres) | One conditional `UPDATE`; row locks serialise writers and the `WHERE` is re-checked | 40 parallel redirects on a 10-click link: exactly 10 succeed |
| Audit chain never forks (Postgres) | Read-last-hash-and-append under a transaction-scoped advisory lock | 200 appends from concurrent writers: unbroken chain |
| Only duplicate codes are 409 (Postgres) | Unique violation on `links_pkey` only | Another unique constraint is re-raised, not mapped to 409 |
| Schema changes (Postgres) | Alembic revisions named after the SQLite schema version; applied on start | Parity test with SQLite; full downgrade to empty and back up |
| Shared rate limits (Redis) | Same GCRA as in memory, one atomic Lua script, Redis clock | Two instances share one allowance |
| Fast lookups (Redis) | Read-through cache with TTL; negative caching | Hits served without the database |
| Takedowns apply everywhere (Redis) | Deactivation deletes the cache entry | Takedown on instance A is a 404 on instance B at once |
| Cheap code scanning (Redis) | Bloom filter (2 MiB, 7 hashes, ~1% false positives at 1.7M codes), used only once fully built | Unknown codes answered with no database query |

## Failure behaviour

| Component down | Behaviour | Why |
|---|---|---|
| Postgres | `/readyz` returns 503; requests fail | The database is the source of truth |
| Redis (cache, Bloom filter) | Falls back to the database; warning logged | The cache is an optimisation, never a dependency |
| Redis (rate limiter) | **Fails open** (requests allowed); warning logged | An outage of the limiter must not take the service down; no limiting during the outage |

## Trade-offs

- A cached link can show a `click_count` up to the cache TTL old; caps are unaffected (enforced in the database).
- Rate limits use one Redis round trip per request; acceptable here, and the reason for an atomic script.
- Migrations run on start for simplicity; for zero-downtime deployments run them as a separate step
  (`urlshort.adapters.postgres.migrate(url)`) before rolling out new instances. All migrations are additive.
