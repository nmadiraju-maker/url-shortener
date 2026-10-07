"""Business logic. Framework-free: no FastAPI imports, so it is testable without HTTP."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from .audit import AuditTrail
from .codegen import random_code
from .config import Settings
from .errors import AliasConflict, CodeSpaceExhausted, LinkExpired, NotFound
from .storage import Link, Repository
from .validation import validate_alias, validate_ttl, validate_url

log = logging.getLogger("urlshort.service")
MAX_CODE_ATTEMPTS = 5


def utcnow() -> datetime:
    return datetime.now(UTC)


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
                ttl_seconds: int | None = None) -> tuple[Link, bool]:
        """Create a link and return (link, created).

        Idempotent for a repeated (owner, url) with no alias and no TTL: the existing permanent link
        is returned with created=False, so client retries never create duplicates.
        """
        target = validate_url(url, max_length=self.settings.max_url_length,
                              blocked_domains=self.settings.blocked_domains,
                              shorteners=self.settings.known_shorteners, self_host=self._self_host)
        ttl = validate_ttl(ttl_seconds, max_ttl=self.settings.max_ttl_seconds)
        if alias is None and ttl is None:
            existing = self.repo.find_reusable_link(owner, target)
            if existing is not None:
                return existing, False
        now = self.clock()
        expires = now + timedelta(seconds=ttl) if ttl else None
        if alias is not None:
            link = self._new_link(validate_alias(alias), target, owner, now, expires)
            self.repo.insert_link(link)  # duplicate alias -> AliasConflict from the database
        else:
            link = self._insert_with_generated_code(target, owner, now, expires)
        self.audit.record(when=now, actor=owner, action="link.create", target=link.code,
                          details={"target_url": target, "custom_alias": alias is not None,
                                   "expires_at": expires.isoformat() if expires else None})
        log.info("link created", extra={"code": link.code, "custom_alias": alias is not None})
        return link, True

    def resolve(self, code: str) -> str:
        """Return the target URL for an active, unexpired link."""
        link = self._get_active(code)
        if link.expires_at is not None and self.clock() >= link.expires_at:
            raise LinkExpired(f"link '{code}' has expired")
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

    # ---------------------------------------------------------------- helpers
    def _get_active(self, code: str) -> Link:
        link = self.repo.get_link(code)
        if link is None or not link.is_active:
            raise NotFound(f"link '{code}' not found")
        return link

    @staticmethod
    def _new_link(code: str, target: str, owner: str, now: datetime, expires: datetime | None) -> Link:
        return Link(code=code, target_url=target, owner=owner, created_at=now, expires_at=expires,
                    is_active=True, click_count=0)

    def _insert_with_generated_code(self, target: str, owner: str, now: datetime,
                                    expires: datetime | None) -> Link:
        """Insert with a fresh random code, retrying a bounded number of times on collision."""
        for attempt in range(1, MAX_CODE_ATTEMPTS + 1):
            link = self._new_link(self.code_factory(self.settings.code_length), target, owner, now, expires)
            try:
                self.repo.insert_link(link)
                return link
            except AliasConflict:
                log.warning("short code collision, retrying", extra={"attempt": attempt})
        raise CodeSpaceExhausted("could not allocate a unique short code; please retry")
