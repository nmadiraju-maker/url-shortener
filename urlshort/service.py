"""Business logic. Framework-free: no FastAPI imports, so it is testable without HTTP."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from . import analytics
from .audit import AuditTrail
from .codegen import random_code
from .config import Settings
from .errors import AliasConflict, CodeSpaceExhausted, LinkExhausted, LinkExpired, NotFound, StorageUnavailable
from .storage import Click, Link, Repository
from .validation import validate_alias, validate_max_clicks, validate_ttl, validate_url

log = logging.getLogger("urlshort.service")
MAX_CODE_ATTEMPTS = 5


def utcnow() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    """Stats tokens are 256-bit random values, so a fast hash is enough (nothing to brute-force);
    storing only the hash means a database leak does not reveal usable tokens."""
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class CreateResult:
    link: Link
    created: bool
    stats_token: str | None   # returned once, on creation only; None when an existing link is reused


class ShortenerService:
    def __init__(self, repo: Repository, settings: Settings, *, clock: Callable[[], datetime] = utcnow,
                 code_factory: Callable[[int], str] = random_code) -> None:
        self.repo = repo
        self.settings = settings
        self.clock = clock
        self.code_factory = code_factory
        self.audit = AuditTrail(repo)
        self._self_host = urlsplit(settings.base_url).hostname

    # ---------------------------------------------------------------- commands
    def shorten(self, url: str, *, owner: str, alias: str | None = None,
                ttl_seconds: int | None = None, max_clicks: int | None = None) -> CreateResult:
        """Create a link.

        Idempotent for a repeated (owner, url) with no alias and no TTL: the existing permanent link
        is returned with created=False, so client retries never create duplicates. A new stats token
        is generated only for new links and is never retrievable later.
        """
        target = validate_url(url, max_length=self.settings.max_url_length,
                              blocked_domains=self.settings.blocked_domains,
                              shorteners=self.settings.known_shorteners, self_host=self._self_host)
        ttl = validate_ttl(ttl_seconds, max_ttl=self.settings.max_ttl_seconds)
        limit = validate_max_clicks(max_clicks)
        if alias is None and ttl is None and limit is None:   # only plain permanent links are reused
            existing = self.repo.find_reusable_link(owner, target)
            if existing is not None:
                return CreateResult(existing, created=False, stats_token=None)
        now = self.clock()
        expires = now + timedelta(seconds=ttl) if ttl else None
        token = secrets.token_urlsafe(32)
        token_hash = hash_token(token)
        if alias is not None:
            link = self._new_link(validate_alias(alias), target, owner, now, expires, token_hash, limit)
            self.repo.insert_link(link)  # duplicate alias -> AliasConflict from the database
        else:
            link = self._insert_with_generated_code(target, owner, now, expires, token_hash, limit)
        self.audit.record(when=now, actor=owner, action="link.create", target=link.code,
                          details={"target_url": target, "custom_alias": alias is not None,
                                   "expires_at": expires.isoformat() if expires else None,
                                   "max_clicks": limit})
        log.info("link created", extra={"code": link.code, "custom_alias": alias is not None})
        return CreateResult(link, created=True, stats_token=token)

    def resolve(self, code: str, *, referrer: str | None = None, user_agent: str | None = None,
                client_ip: str | None = None) -> str:
        """Return the target URL for an active, unexpired link and record the click."""
        link = self._get_active(code)
        now = self.clock()
        if link.expires_at is not None and now >= link.expires_at:
            raise LinkExpired(f"link '{code}' has expired")
        family, is_bot = analytics.agent_family(user_agent)
        ip_id, key_id = analytics.visitor_id(client_ip, self.settings.ip_hash_salt, now)
        click = Click(code=code, ts=now, referrer_host=analytics.referrer_host(referrer), agent_family=family,
                      is_bot=is_bot, ip_id=ip_id, ip_key_id=key_id)
        if link.max_clicks is not None:
            # Capped link: the count IS the business rule, so check-and-count atomically and fail closed
            # (503) if storage is unavailable. Never trust the snapshot read above.
            try:
                allowed = self.repo.record_click_with_limit(click)
            except Exception as exc:
                log.exception("click limit check failed", extra={"code": code})
                raise StorageUnavailable("could not verify the click limit; please retry") from exc
            if not allowed:
                raise LinkExhausted(f"link '{code}' has reached its click limit")
            return link.target_url
        try:
            self.repo.record_click(click)
        except Exception:  # uncapped: fail open, analytics must never break a redirect
            log.exception("click recording failed", extra={"code": code})
        return link.target_url

    def deactivate(self, code: str, *, actor: str) -> None:
        if not self.repo.deactivate(code):
            raise NotFound(f"link '{code}' not found")
        self.audit.record(when=self.clock(), actor=actor, action="link.deactivate", target=code, details={})
        log.info("link deactivated", extra={"code": code, "actor": actor})

    # ---------------------------------------------------------------- queries
    def get(self, code: str) -> Link:
        link = self.repo.get_link(code)
        if link is None:
            raise NotFound(f"link '{code}' not found")
        return link

    def can_view_stats(self, code: str, token: str | None) -> bool:
        """True if the token belongs to this link. Constant-time compare; False for unknown links too,
        so a caller without a valid token cannot learn which codes exist."""
        link = self.repo.get_link(code)
        if link is None or link.stats_token_hash is None or not token:
            return False
        return hmac.compare_digest(link.stats_token_hash, hash_token(token))

    def stats(self, code: str) -> dict[str, Any]:
        link = self.get(code)
        return {"code": link.code, **analytics.summarise(self.repo.clicks_for(code))}

    # ---------------------------------------------------------------- helpers
    def _get_active(self, code: str) -> Link:
        link = self.repo.get_link(code)
        if link is None or not link.is_active:
            raise NotFound(f"link '{code}' not found")
        return link

    @staticmethod
    def _new_link(code: str, target: str, owner: str, now: datetime, expires: datetime | None,
                  token_hash: str | None, max_clicks: int | None = None) -> Link:
        return Link(code=code, target_url=target, owner=owner, created_at=now, expires_at=expires,
                    is_active=True, click_count=0, stats_token_hash=token_hash, max_clicks=max_clicks)

    def _insert_with_generated_code(self, target: str, owner: str, now: datetime,
                                    expires: datetime | None, token_hash: str | None,
                                    max_clicks: int | None = None) -> Link:
        """Insert with a fresh random code, retrying a bounded number of times on collision."""
        for attempt in range(1, MAX_CODE_ATTEMPTS + 1):
            code = self.code_factory(self.settings.code_length)
            link = self._new_link(code, target, owner, now, expires, token_hash, max_clicks)
            try:
                self.repo.insert_link(link)
                return link
            except AliasConflict:
                log.warning("short code collision, retrying", extra={"attempt": attempt})
        raise CodeSpaceExhausted("could not allocate a unique short code; please retry")
