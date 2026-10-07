"""Business rules tested without HTTP."""
import logging
import sqlite3
from collections.abc import Iterator

import pytest

from urlshort.audit import AuditTrail
from urlshort.config import Settings
from urlshort.errors import AliasConflict, CodeSpaceExhausted, InvalidInput, LinkExpired, NotFound
from urlshort.service import ShortenerService, hash_token, utcnow
from urlshort.storage import Click, Link, SqliteRepository

from .helpers import FakeClock


@pytest.fixture
def svc(repo: SqliteRepository, clock: FakeClock) -> Iterator[ShortenerService]:
    yield ShortenerService(repo, Settings(base_url="https://sho.rt", ip_hash_salt="test-salt"), clock=clock)


def test_create_resolve_and_audit(svc: ShortenerService, repo: SqliteRepository) -> None:
    result = svc.shorten("https://Example.com/A", owner="team-a")
    link = result.link
    assert result.created and link.target_url == "https://example.com/A" and len(link.code) == 7
    assert svc.resolve(link.code) == "https://example.com/A"
    record = repo.audit_records()[0]
    assert (record.actor, record.action, record.target) == ("team-a", "link.create", link.code)


def test_stats_token_is_returned_once_and_stored_only_as_hash(svc: ShortenerService, repo: SqliteRepository) -> None:
    result = svc.shorten("https://example.com/t", owner="team-a")
    assert result.stats_token and len(result.stats_token) >= 40
    stored = repo.get_link(result.link.code)
    assert stored is not None and stored.stats_token_hash == hash_token(result.stats_token)
    assert result.stats_token not in str(stored)
    reused = svc.shorten("https://example.com/t", owner="team-a")
    assert not reused.created and reused.stats_token is None          # never re-issued


def test_idempotent_only_for_same_owner_and_permanent_links(svc: ShortenerService) -> None:
    first = svc.shorten("https://example.com/x", owner="team-a").link
    again = svc.shorten("https://example.com/x", owner="team-a")
    other = svc.shorten("https://example.com/x", owner="team-b").link
    timed = svc.shorten("https://example.com/x", owner="team-a", ttl_seconds=60)
    assert (again.link.code, again.created) == (first.code, False)
    assert other.code != first.code and timed.created and timed.link.code != first.code


def test_permanent_request_never_returns_an_expiring_link(svc: ShortenerService) -> None:
    expiring = svc.shorten("https://example.com/promo", owner="team-a", ttl_seconds=60).link
    permanent = svc.shorten("https://example.com/promo", owner="team-a")
    assert permanent.created and permanent.link.code != expiring.code and permanent.link.expires_at is None


def test_expiry(svc: ShortenerService, clock: FakeClock) -> None:
    link = svc.shorten("https://example.com/t", owner="o", ttl_seconds=60).link
    clock.advance(59)
    assert svc.resolve(link.code) == "https://example.com/t"
    clock.advance(1)
    with pytest.raises(LinkExpired):
        svc.resolve(link.code)


def test_custom_alias_rules(svc: ShortenerService) -> None:
    assert svc.shorten("https://example.com/1", owner="o", alias="spring-sale").link.code == "spring-sale"
    with pytest.raises(AliasConflict):
        svc.shorten("https://example.com/2", owner="o", alias="spring-sale")
    with pytest.raises(InvalidInput, match="reserved"):
        svc.shorten("https://example.com/3", owner="o", alias="healthz")


def test_own_host_is_rejected(svc: ShortenerService) -> None:
    with pytest.raises(InvalidInput, match="shorteners"):
        svc.shorten("https://sho.rt/abc1234", owner="o")


def test_collision_retry_then_success(repo: SqliteRepository, clock: FakeClock) -> None:
    codes = iter(["AAAAAAA", "AAAAAAA", "BBBBBBB"])
    svc = ShortenerService(repo, Settings(), clock=clock, code_factory=lambda n: next(codes))
    assert svc.shorten("https://example.com/1", owner="o").link.code == "AAAAAAA"
    assert svc.shorten("https://example.com/2", owner="o").link.code == "BBBBBBB"


def test_collision_retries_are_bounded(repo: SqliteRepository, clock: FakeClock) -> None:
    svc = ShortenerService(repo, Settings(), clock=clock, code_factory=lambda n: "SAMECOD")
    svc.shorten("https://example.com/1", owner="o")
    with pytest.raises(CodeSpaceExhausted):
        svc.shorten("https://example.com/2", owner="o")


def test_deactivate(svc: ShortenerService, repo: SqliteRepository) -> None:
    link = svc.shorten("https://example.com/d", owner="o").link
    svc.deactivate(link.code, actor="admin")
    with pytest.raises(NotFound):
        svc.resolve(link.code)
    assert svc.get(link.code).is_active is False          # still visible for history
    with pytest.raises(NotFound):
        svc.deactivate(link.code, actor="admin")
    assert [r.action for r in repo.audit_records()] == ["link.create", "link.deactivate"]
    assert AuditTrail(repo).verify()


def test_unknown_code(svc: ShortenerService) -> None:
    for call in (lambda: svc.get("nope"), lambda: svc.resolve("nope"), lambda: svc.stats("nope")):
        with pytest.raises(NotFound):
            call()


# ---------------- click recording and stats
def test_resolve_records_enriched_click_without_raw_ip(svc: ShortenerService, repo: SqliteRepository) -> None:
    link = svc.shorten("https://example.com/c", owner="o").link
    svc.resolve(link.code, referrer="https://News.example/post/1?user=42", user_agent="Mozilla/5.0 Chrome/126",
                client_ip="203.0.113.7")
    (click,) = repo.clicks_for(link.code)
    assert (click.referrer_host, click.agent_family, click.is_bot) == ("news.example", "chrome", False)
    assert isinstance(click.ip_id, int) and click.ip_key_id == "2026-01-15"
    assert "203.0.113.7" not in str(click)


def test_stats_separate_bots_and_count_daily_uniques(svc: ShortenerService, clock: FakeClock) -> None:
    code = svc.shorten("https://example.com/s", owner="o").link.code
    svc.resolve(code, user_agent="Mozilla/5.0 Chrome/126", client_ip="203.0.113.7")
    svc.resolve(code, user_agent="Mozilla/5.0 Chrome/126", client_ip="203.0.113.7")
    svc.resolve(code, user_agent="Googlebot/2.1", client_ip="66.249.66.1")
    clock.advance(86400)
    svc.resolve(code, referrer="https://news.example/", user_agent="Firefox/128", client_ip="203.0.113.7")
    stats = svc.stats(code)
    assert stats["code"] == code and stats["total_clicks"] == 3 and stats["bot_clicks"] == 1
    assert stats["clicks_by_day"] == {"2026-01-15": 2, "2026-01-16": 1}
    assert stats["unique_visitors_by_day"] == {"2026-01-15": 1, "2026-01-16": 1}
    assert stats["agents"] == {"chrome": 2, "firefox": 1}
    assert stats["top_referrers"][0] == {"host": "direct", "clicks": 2}


def test_redirect_survives_analytics_failure(svc: ShortenerService, repo: SqliteRepository,
                                             caplog: pytest.LogCaptureFixture) -> None:
    link = svc.shorten("https://example.com/r", owner="o").link

    def broken(click: Click) -> None:
        raise sqlite3.OperationalError("disk full")
    repo.record_click = broken  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="urlshort.service"):
        assert svc.resolve(link.code) == "https://example.com/r"
    assert "click recording failed" in caplog.text


def test_can_view_stats(svc: ShortenerService, repo: SqliteRepository, clock: FakeClock) -> None:
    result = svc.shorten("https://example.com/v", owner="o")
    code, token = result.link.code, result.stats_token
    assert svc.can_view_stats(code, token) is True
    assert svc.can_view_stats(code, token + "x") is False             # type: ignore[operator]
    assert svc.can_view_stats(code, None) is False and svc.can_view_stats(code, "") is False
    assert svc.can_view_stats("unknown", token) is False
    repo.insert_link(Link(code="legacy1", target_url="https://example.com/l", owner="o", created_at=clock(),
                          expires_at=None, is_active=True, click_count=0))   # created before tokens existed
    assert svc.can_view_stats("legacy1", token) is False


def test_utcnow_is_timezone_aware() -> None:
    assert utcnow().tzinfo is not None
