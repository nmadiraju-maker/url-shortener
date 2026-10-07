"""Business rules tested without HTTP."""
from collections.abc import Iterator

import pytest

from tests.service.conftest import FakeClock
from urlshort.audit import AuditTrail
from urlshort.config import Settings
from urlshort.errors import AliasConflict, CodeSpaceExhausted, InvalidInput, LinkExpired, NotFound
from urlshort.service import ShortenerService, utcnow
from urlshort.storage import SqliteRepository


@pytest.fixture
def svc(repo: SqliteRepository, clock: FakeClock) -> Iterator[ShortenerService]:
    yield ShortenerService(repo, Settings(base_url="https://sho.rt"), clock=clock)


def test_create_resolve_and_audit(svc: ShortenerService, repo: SqliteRepository) -> None:
    link, created = svc.shorten("https://Example.com/A", owner="team-a")
    assert created and link.target_url == "https://example.com/A" and len(link.code) == 7
    assert svc.resolve(link.code) == "https://example.com/A"
    record = repo.audit_records()[0]
    assert (record.actor, record.action, record.target) == ("team-a", "link.create", link.code)


def test_idempotent_only_for_same_owner_and_permanent_links(svc: ShortenerService) -> None:
    first, _ = svc.shorten("https://example.com/x", owner="team-a")
    again, created = svc.shorten("https://example.com/x", owner="team-a")
    other, _ = svc.shorten("https://example.com/x", owner="team-b")
    timed, timed_created = svc.shorten("https://example.com/x", owner="team-a", ttl_seconds=60)
    assert (again.code, created) == (first.code, False)
    assert other.code != first.code and timed_created and timed.code != first.code


def test_permanent_request_never_returns_an_expiring_link(svc: ShortenerService) -> None:
    expiring, _ = svc.shorten("https://example.com/promo", owner="team-a", ttl_seconds=60)
    permanent, created = svc.shorten("https://example.com/promo", owner="team-a")
    assert created and permanent.code != expiring.code and permanent.expires_at is None


def test_expiry(svc: ShortenerService, clock: FakeClock) -> None:
    link, _ = svc.shorten("https://example.com/t", owner="o", ttl_seconds=60)
    clock.advance(59)
    assert svc.resolve(link.code) == "https://example.com/t"
    clock.advance(1)
    with pytest.raises(LinkExpired):
        svc.resolve(link.code)


def test_custom_alias_rules(svc: ShortenerService) -> None:
    assert svc.shorten("https://example.com/1", owner="o", alias="spring-sale")[0].code == "spring-sale"
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
    assert svc.shorten("https://example.com/1", owner="o")[0].code == "AAAAAAA"
    assert svc.shorten("https://example.com/2", owner="o")[0].code == "BBBBBBB"


def test_collision_retries_are_bounded(repo: SqliteRepository, clock: FakeClock) -> None:
    svc = ShortenerService(repo, Settings(), clock=clock, code_factory=lambda n: "SAMECOD")
    svc.shorten("https://example.com/1", owner="o")
    with pytest.raises(CodeSpaceExhausted):
        svc.shorten("https://example.com/2", owner="o")


def test_deactivate(svc: ShortenerService, repo: SqliteRepository) -> None:
    link, _ = svc.shorten("https://example.com/d", owner="o")
    svc.deactivate(link.code, actor="admin")
    with pytest.raises(NotFound):
        svc.resolve(link.code)
    assert svc.get(link.code).is_active is False          # still visible for history
    with pytest.raises(NotFound):
        svc.deactivate(link.code, actor="admin")
    assert [r.action for r in repo.audit_records()] == ["link.create", "link.deactivate"]
    assert AuditTrail(repo).verify()


def test_unknown_code(svc: ShortenerService) -> None:
    for call in (lambda: svc.get("nope"), lambda: svc.resolve("nope")):
        with pytest.raises(NotFound):
            call()


def test_utcnow_is_timezone_aware() -> None:
    assert utcnow().tzinfo is not None
