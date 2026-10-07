"""Input validation and abuse guardrails for target URLs, aliases and expiry times.

Plain functions with no framework imports, so the same rules apply however the service is called.
Everything fails closed: input that does not clearly pass is rejected with InvalidInput.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from .errors import InvalidInput

ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
# Paths the service itself needs; a user must never be able to claim them as short codes.
RESERVED_ALIASES = frozenset({"api", "admin", "healthz", "readyz", "livez", "docs", "openapi.json", "redoc",
                              "static", "metrics"})
ALLOWED_SCHEMES = frozenset({"http", "https"})
# Redirect chains through other shorteners hide the real destination. Configurable via Settings.
KNOWN_SHORTENERS = frozenset({"bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly",
                              "rebrand.ly"})


def _is_internal_host(host: str) -> bool:
    """True for hosts that must never be a redirect target (SSRF protection)."""
    if host == "localhost" or host.endswith((".localhost", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified


def _matches(host: str, domains: frozenset[str]) -> bool:
    """True if host is one of the domains or a subdomain of one."""
    return any(host == d or host.endswith("." + d) for d in domains)


def validate_url(url: str, *, max_length: int, blocked_domains: frozenset[str] = frozenset(),
                 shorteners: frozenset[str] = KNOWN_SHORTENERS, self_host: str | None = None) -> str:
    """Return a normalised URL (lower-case scheme and host) or raise InvalidInput."""
    candidate = (url or "").strip()
    if not candidate:
        raise InvalidInput("url is required")
    if len(candidate) > max_length:
        raise InvalidInput(f"url exceeds {max_length} characters")
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidInput("only http and https URLs are allowed")
    if not parts.hostname:
        raise InvalidInput("url must include a host")
    if parts.username or parts.password:
        raise InvalidInput("credentials in URLs are not allowed")
    host = parts.hostname.lower()
    if _is_internal_host(host):
        raise InvalidInput("internal or private hosts are not allowed")
    if _matches(host, blocked_domains):
        raise InvalidInput("target domain is blocked")
    denied = shorteners | ({self_host.lower()} if self_host else frozenset())
    if _matches(host, denied):
        raise InvalidInput("links to other URL shorteners are not allowed (redirect chains hide the destination)")
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return parts._replace(scheme=parts.scheme.lower(), netloc=netloc).geturl()  # path stays case-sensitive


def validate_alias(alias: str) -> str:
    if not ALIAS_RE.fullmatch(alias):
        raise InvalidInput("alias must be 3-32 characters of letters, digits, '-' or '_'")
    if alias.lower() in RESERVED_ALIASES:
        raise InvalidInput("alias is reserved")
    return alias


def validate_ttl(ttl_seconds: int | None, *, max_ttl: int) -> int | None:
    """None means a permanent link; otherwise the TTL must be between 1 second and max_ttl."""
    if ttl_seconds is None:
        return None
    if ttl_seconds <= 0 or ttl_seconds > max_ttl:
        raise InvalidInput(f"ttl_seconds must be between 1 and {max_ttl}")
    return ttl_seconds
