"""Read-through link cache and a Bloom filter in Redis, wrapped around any Repository.

* get_link: Bloom filter first ("definitely not a code" -> no database query, which makes code scanning
  cheap), then the cache, then the database. Found links are cached for `ttl` seconds; misses that got
  past the Bloom filter (false positives) are cached briefly as negatives.
* Creating a link adds it to the Bloom filter and clears any cached negative; deactivating a link deletes
  its cache entry, so a takedown applies on every instance immediately.
* Cached links may show a click_count up to `ttl` seconds old. Click caps are unaffected: they are
  enforced by the database's conditional UPDATE, never by the cached value.
* The Bloom filter is ignored until it has been fully built from the database: a half-built filter would
  wrongly report existing links as missing.
* Any Redis error falls back to the database. The cache is an optimisation, never a dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from ..storage import ApiKey, AuditRecord, ChainFn, Click, Link, Repository
from ..throttled import ThrottledLog

log = logging.getLogger("urlshort.cache")
NEGATIVE = b"-"


class BloomFilter:
    """Redis bitmap Bloom filter. Default 2^24 bits (2 MiB) with 7 hashes: ~1% false positives at 1.7M codes."""

    def __init__(self, client: Redis, key: str = "urlshort:bloom", bits: int = 1 << 24, hashes: int = 7) -> None:
        self._r, self._key, self._ready = client, key, key + ":ready"
        self._bits, self._hashes = bits, hashes

    def _positions(self, item: str) -> list[int]:
        digest = hashlib.sha256(item.encode()).digest()
        h1, h2 = int.from_bytes(digest[:8], "big"), int.from_bytes(digest[8:16], "big") | 1
        return [(h1 + i * h2) % self._bits for i in range(self._hashes)]

    def add(self, item: str) -> None:
        pipe = self._r.pipeline(transaction=False)
        for pos in self._positions(item):
            pipe.setbit(self._key, pos, 1)
        pipe.execute()

    def might_contain(self, item: str) -> bool:
        if not self._r.exists(self._ready):
            return True                                    # not built yet: cannot rule anything out
        pipe = self._r.pipeline(transaction=False)
        for pos in self._positions(item):
            pipe.getbit(self._key, pos)
        return all(pipe.execute())

    def build(self, codes: Callable[[], list[str]]) -> bool:
        """Populate from the database once (one instance does it; others wait for it). True if built here."""
        if self._r.exists(self._ready) or not self._r.set(self._key + ":building", 1, nx=True, ex=300):
            return False
        for code in codes():
            self.add(code)
        self._r.set(self._ready, 1)
        self._r.delete(self._key + ":building")
        return True


def _encode(link: Link) -> str:
    data = dict(link.__dict__)
    for field in ("created_at", "expires_at"):
        data[field] = data[field].isoformat() if data[field] else None
    return json.dumps(data)


def _decode(raw: bytes) -> Link:
    data: dict[str, Any] = json.loads(raw)
    for field in ("created_at", "expires_at"):
        data[field] = datetime.fromisoformat(data[field]) if data[field] else None
    return Link(**data)


class CachedRepository:
    def __init__(self, repo: Repository, client: Redis, *, ttl: int = 60, negative_ttl: int = 30,
                 bloom: BloomFilter | None = None) -> None:
        self._repo, self._r = repo, client
        self._warn = ThrottledLog(log, "cache unavailable; using the database")
        self._ttl, self._negative_ttl = ttl, negative_ttl
        self.bloom = bloom

    @staticmethod
    def _key(code: str) -> str:
        return f"urlshort:link:{code}"

    def _safely(self, action: Callable[[], Any], default: Any = None) -> Any:
        try:
            return action()
        except RedisError:
            self._warn()
            return default

    # ---------------------------------------------------------------- links
    def get_link(self, code: str) -> Link | None:
        bloom = self.bloom
        if bloom is not None and not self._safely(lambda: bloom.might_contain(code), True):
            return None
        cached = self._safely(lambda: self._r.get(self._key(code)))
        if cached == NEGATIVE:
            return None
        if cached:
            return _decode(cached)
        link = self._repo.get_link(code)
        value, ttl = (_encode(link), self._ttl) if link else (NEGATIVE, self._negative_ttl)
        self._safely(lambda: self._r.set(self._key(code), value, ex=ttl))
        return link

    def insert_link(self, link: Link) -> None:
        self._repo.insert_link(link)
        bloom = self.bloom
        if bloom is not None:
            self._safely(lambda: bloom.add(link.code))
        self._safely(lambda: self._r.delete(self._key(link.code)))

    def deactivate(self, code: str) -> bool:
        changed = self._repo.deactivate(code)
        self._safely(lambda: self._r.delete(self._key(code)))
        return changed

    def find_reusable_link(self, owner: str, target_url: str) -> Link | None:
        return self._repo.find_reusable_link(owner, target_url)

    def all_codes(self) -> list[str]:
        return self._repo.all_codes()

    def insert_api_key(self, key: ApiKey) -> None:
        self._repo.insert_api_key(key)

    def get_api_key(self, key_id: str) -> ApiKey | None:
        return self._repo.get_api_key(key_id)

    def list_api_keys(self) -> list[ApiKey]:
        return self._repo.list_api_keys()

    def revoke_api_key(self, key_id: str, at: datetime) -> bool:
        return self._repo.revoke_api_key(key_id, at)

    # ---------------------------------------------------------------- pass-through
    def record_click(self, click: Click, *, as_event: bool = False) -> None:
        self._repo.record_click(click, as_event=as_event)

    def record_click_with_limit(self, click: Click, *, as_event: bool = False) -> bool:
        return self._repo.record_click_with_limit(click, as_event=as_event)

    def outbox_pending(self, limit: int) -> list[tuple[int, str]]:
        return self._repo.outbox_pending(limit)

    def mark_published(self, ids: list[int], at: datetime) -> None:
        self._repo.mark_published(ids, at)

    def outbox_backlog(self) -> int:
        return self._repo.outbox_backlog()

    def apply_click_event(self, click: Click) -> bool:
        return self._repo.apply_click_event(click)

    def clicks_for(self, code: str) -> list[Click]:
        return self._repo.clicks_for(code)

    def append_audit(self, ts: str, actor: str, action: str, target: str, details: str, chain: ChainFn) -> str:
        return self._repo.append_audit(ts, actor, action, target, details, chain)

    def audit_records(self) -> list[AuditRecord]:
        return self._repo.audit_records()

    def ping(self) -> bool:
        return self._repo.ping()
