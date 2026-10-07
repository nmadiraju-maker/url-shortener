"""Everything the routers and middleware share for one app instance."""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass

from starlette.requests import Request

from ..config import Settings
from ..errors import RateLimited
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

    def is_admin(self, x_api_key: str | None) -> bool:
        # No configured key means admin access is disabled, never open.
        # compare_digest takes the same time however close a guess is (no timing side channel).
        key = self.settings.admin_api_key
        return bool(key and x_api_key and hmac.compare_digest(x_api_key, key))

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
                            is_active=link.is_active, click_count=link.click_count)
