"""Click enrichment and aggregation. Pure functions: no database, no HTTP.

Privacy model
  * The raw client IP is never stored or logged. Each click stores `ip_id`: the first 64 bits of
    HMAC-SHA256(daily_key, ip), plus `ip_key_id` (the UTC date of the key).
  * daily_key = HMAC-SHA256(master_key, "ip-id/v1/" + date). A new key every day means one visitor
    cannot be followed across days, and a leaked daily key exposes only that day.
  * HMAC, not sha256(salt + ip): it is the standard construction for keyed hashing.
  * Unique visitors are therefore counted per day (the honest unit with daily keys).
"""

from __future__ import annotations

import hashlib
import hmac
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from .storage import Click

# Checked in order: bots first, then specific browsers before generic ones (Edge's user agent also
# contains "Chrome", and Chrome's also contains "Safari").
BOT_MARKERS = ("bot", "crawler", "spider", "slurp", "facebookexternalhit", "preview", "curl", "wget",
               "python-requests", "httpclient", "headless")
BROWSERS = (("edg/", "edge"), ("opr/", "opera"), ("firefox/", "firefox"), ("chrome/", "chrome"), ("safari/", "safari"))
TOP_N = 5


def agent_family(user_agent: str | None) -> tuple[str, bool]:
    """Return (family, is_bot) from the User-Agent header. Easy to spoof: a heuristic, not security."""
    ua = (user_agent or "").lower()
    if not ua:
        return "unknown", False
    if any(marker in ua for marker in BOT_MARKERS):
        return "bot", True
    for needle, family in BROWSERS:
        if needle in ua:
            return family, False
    return "other", False


def referrer_host(referrer: str | None) -> str | None:
    """Keep only the referring domain: paths and query strings can carry personal data."""
    if not referrer:
        return None
    host = urlsplit(referrer.strip()).hostname
    return host.lower() if host else None


def _daily_key(master_key: str, day: str) -> bytes:
    return hmac.new(master_key.encode(), f"ip-id/v1/{day}".encode(), hashlib.sha256).digest()


def visitor_id(ip: str | None, master_key: str, at: datetime) -> tuple[int | None, str | None]:
    """Return (ip_id, ip_key_id) for a client IP at a moment in time, or (None, None) without an IP."""
    if not ip:
        return None, None
    day = at.astimezone(UTC).date().isoformat()
    digest = hmac.new(_daily_key(master_key, day), ip.encode(), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big", signed=True), day   # signed: fits SQLite's 64-bit INTEGER


def summarise(clicks: Sequence[Click]) -> dict[str, Any]:
    """Aggregate clicks for the stats endpoint. Bots are counted separately and excluded elsewhere."""
    human = [c for c in clicks if not c.is_bot]
    by_day = Counter(c.ts.astimezone(UTC).date().isoformat() for c in human)
    uniques: dict[str, set[int]] = {}
    for c in human:
        if c.ip_id is not None and c.ip_key_id is not None:
            uniques.setdefault(c.ip_key_id, set()).add(c.ip_id)
    referrers = Counter(c.referrer_host or "direct" for c in human)
    agents = Counter(c.agent_family for c in human)
    return {
        "total_clicks": len(human),
        "bot_clicks": len(clicks) - len(human),
        "clicks_by_day": dict(sorted(by_day.items())),
        "unique_visitors_by_day": {day: len(ids) for day, ids in sorted(uniques.items())},
        "top_referrers": [{"host": host, "clicks": n} for host, n in referrers.most_common(TOP_N)],
        "agents": dict(sorted(agents.items())),
        "last_click_at": max((c.ts for c in human), default=None),
    }
