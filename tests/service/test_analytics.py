from datetime import UTC, datetime, timedelta, timezone

import pytest

from urlshort.analytics import agent_family, referrer_host, summarise, visitor_id
from urlshort.storage import Click

T0 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("ua,expected", [
    (None, ("unknown", False)),
    ("", ("unknown", False)),
    ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/126.0 Safari/537.36", ("chrome", False)),
    ("Mozilla/5.0 AppleWebKit/537.36 Chrome/126.0 Safari/537.36 Edg/126.0", ("edge", False)),
    ("Mozilla/5.0 AppleWebKit/537.36 Chrome/126.0 Safari/537.36 OPR/111.0", ("opera", False)),
    ("Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15", ("safari", False)),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0", ("firefox", False)),
    ("SomethingElse/1.0", ("other", False)),
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", ("bot", True)),
    ("facebookexternalhit/1.1", ("bot", True)),
    ("Slackbot-LinkExpanding 1.0", ("bot", True)),
    ("curl/8.5.0", ("bot", True)),
    ("python-requests/2.32", ("bot", True)),
    ("Mozilla/5.0 HeadlessChrome/126.0", ("bot", True)),
])
def test_agent_family(ua: str | None, expected: tuple[str, bool]) -> None:
    assert agent_family(ua) == expected


@pytest.mark.parametrize("ref,expected", [
    (None, None), ("", None), ("not a url", None),
    ("https://News.Example/post/1?user=42#x", "news.example"), ("  http://a.example:8080/p  ", "a.example"),
])
def test_referrer_host_keeps_only_the_domain(ref: str | None, expected: str | None) -> None:
    assert referrer_host(ref) == expected


def test_visitor_id_properties() -> None:
    same_day = visitor_id("203.0.113.7", "k", T0)
    assert same_day == visitor_id("203.0.113.7", "k", T0 + timedelta(hours=11))      # stable within a day
    assert same_day[1] == "2026-01-15"
    ip_id = same_day[0]
    assert ip_id is not None and -(2 ** 63) <= ip_id < 2 ** 63                        # fits SQLite INTEGER
    assert visitor_id("203.0.113.7", "k", T0 + timedelta(days=1))[0] != ip_id         # new key each day
    assert visitor_id("203.0.113.8", "k", T0)[0] != ip_id                             # different visitor
    assert visitor_id("203.0.113.7", "other-key", T0)[0] != ip_id                     # depends on the secret
    assert visitor_id(None, "k", T0) == (None, None) and visitor_id("", "k", T0) == (None, None)


def test_visitor_day_follows_utc() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    assert visitor_id("1.2.3.4", "k", datetime(2026, 1, 16, 2, 0, tzinfo=ist))[1] == "2026-01-15"


def click(at: datetime, *, bot: bool = False, ref: str | None = None, ip: int | None = 1,
          agent: str = "chrome") -> Click:
    return Click(code="c", ts=at, referrer_host=ref, agent_family="bot" if bot else agent, is_bot=bot,
                 ip_id=ip, ip_key_id=at.date().isoformat() if ip is not None else None)


def test_summarise_empty() -> None:
    assert summarise([]) == {"total_clicks": 0, "bot_clicks": 0, "clicks_by_day": {}, "clicks_by_hour": {},
                             "unique_visitors_by_day": {}, "top_referrers": [], "agents": {}, "last_click_at": None}


def test_summarise_mixed_traffic() -> None:
    later = T0 + timedelta(days=1)
    clicks = [click(T0, ip=1), click(T0, ip=1, ref="a.example"), click(T0, ip=2, agent="firefox"),
              click(T0, bot=True, ip=9), click(later, ip=1, ref="a.example"), click(later, ip=None)]
    s = summarise(clicks)
    assert s["total_clicks"] == 5 and s["bot_clicks"] == 1
    assert s["clicks_by_day"] == {"2026-01-15": 3, "2026-01-16": 2}
    assert s["unique_visitors_by_day"] == {"2026-01-15": 2, "2026-01-16": 1}          # clicks without an IP don't count
    assert s["top_referrers"] == [{"host": "direct", "clicks": 3}, {"host": "a.example", "clicks": 2}]
    assert s["agents"] == {"chrome": 4, "firefox": 1} and s["last_click_at"] == later


def test_summarise_limits_top_referrers_to_five() -> None:
    clicks = [click(T0, ref=f"r{i}.example") for i in range(8)]
    assert len(summarise(clicks)["top_referrers"]) == 5
