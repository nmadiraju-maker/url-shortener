"""Everything the routers and middleware share for one app instance."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from starlette.requests import Request

from ..auth import Principal, authenticate
from ..config import Settings
from ..errors import RateLimited, Unauthorized
from ..models import LinkResponse
from ..ratelimit import RateLimiter
from ..service import ShortenerService
from ..storage import Link, Repository
from .clientip import TrustedProxies, request_client_ip

log = logging.getLogger("urlshort.api")


@dataclass(frozen=True)
class AppContext:
    settings: Settings
    repository: Repository
    service: ShortenerService
    create_limiter: RateLimiter
    redirect_limiter: RateLimiter
    proxies: TrustedProxies

    def client_ip(self, request: Request) -> str | None:
        return request_client_ip(request, self.proxies)

    def principal(self, x_api_key: str | None, authorization: str | None) -> Principal | None:
        """Who is calling: from `X-API-Key` or `Authorization: Bearer <key>`. A key that is PRESENTED but not
        valid raises 401: it is never silently treated as anonymous (e.g. a revoked owner key)."""
        presented = x_api_key or (authorization[7:].strip() if authorization and
                                  authorization.lower().startswith("bearer ") else None)
        if not presented:
            return None
        found = authenticate(self.repository, presented, bootstrap_admin_key=self.settings.admin_api_key)
        if found is None:
            raise Unauthorized("invalid or revoked API key")
        return found

    def enforce(self, limiter: RateLimiter, request: Request, what: str) -> dict[str, str]:
        """Apply a rate limit to this request's client; returns RateLimit-* headers or raises 429."""
        decision = limiter.acquire(self.client_ip(request) or "unknown")
        headers = decision.headers()
        if not decision.allowed:
            log.warning("rate limited", extra={"limit": what, "retry_after": headers["Retry-After"]})
            raise RateLimited(f"too many {what} requests; retry later", int(headers["Retry-After"]), headers)
        return headers

    def to_response(self, link: Link) -> LinkResponse:
        return LinkResponse(code=link.code, short_url=f"{self.settings.base_url}/{link.code}",
                            target_url=link.target_url, created_at=link.created_at, expires_at=link.expires_at,
                            is_active=link.is_active, click_count=link.click_count,
                            max_clicks=link.max_clicks)
