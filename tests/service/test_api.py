"""Functional API tests. Docstrings list the acceptance criteria each test verifies."""
import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from urlshort.api import create_app
from urlshort.config import Settings
from urlshort.logging_setup import JsonFormatter
from urlshort.storage import SqliteRepository

from .helpers import FakeClock

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def create(client: TestClient, url: str = "https://example.com/page", **extra: object) -> dict[str, object]:
    resp = client.post("/api/v1/links", json={"url": url, **extra})
    assert resp.status_code in (200, 201), resp.text
    body: dict[str, object] = resp.json()
    return body


# ---------------- create
def test_create_returns_201_with_short_url(client: TestClient) -> None:
    """AC-SHORTEN-1"""
    resp = client.post("/api/v1/links", json={"url": "https://Example.com/a?b=1"})
    body = resp.json()
    assert resp.status_code == 201 and len(body["code"]) == 7
    assert body["short_url"] == f"https://sho.rt/{body['code']}"
    assert body["target_url"] == "https://example.com/a?b=1"
    assert body["is_active"] is True and body["click_count"] == 0 and body["expires_at"] is None


def test_repeat_create_is_idempotent_per_owner(client: TestClient) -> None:
    """AC-SHORTEN-2"""
    def post(owner: str) -> tuple[int, str]:
        r = client.post("/api/v1/links", json={"url": "https://example.com/x"}, headers={"x-owner": owner})
        return r.status_code, r.json()["code"]
    (s1, c1), (s2, c2), (s3, c3) = post("team-a"), post("team-a"), post("team-b")
    assert (s1, s2, s3) == (201, 200, 201) and c1 == c2 != c3


@pytest.mark.parametrize("payload,fragment", [
    ({"url": "ftp://example.com/file"}, "only http and https"),
    ({"url": "https:///nohost"}, "must include a host"),
    ({"url": ""}, "required"),
    ({"url": "https://example.com/" + "a" * 2050}, "exceeds"),
    ({}, "url: Field required"),
    ({"url": "https://example.com", "ttl_seconds": "soon"}, "ttl_seconds"),
    ({"url": "https://example.com", "ttl": 60}, "ttl: Extra inputs are not permitted"),   # typo is rejected
])
def test_invalid_requests_return_400_envelope(client: TestClient, payload: dict[str, object], fragment: str) -> None:
    """AC-SHORTEN-3"""
    resp = client.post("/api/v1/links", json=payload)
    error = resp.json()["error"]
    assert resp.status_code == 400 and error["code"] == "invalid_input" and fragment in error["message"]


def test_malformed_json_body_is_400(client: TestClient) -> None:
    """AC-SHORTEN-3"""
    resp = client.post("/api/v1/links", content="{not json", headers={"content-type": "application/json"})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "invalid_input"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data", "https://user:pass@bank.example/",
    "https://evil.example/x", "https://bit.ly/abc", "https://sho.rt/abc1234", "javascript:alert(1)",
])
def test_unsafe_targets_are_rejected(client: TestClient, url: str) -> None:
    """AC-SAFETY-1"""
    assert client.post("/api/v1/links", json={"url": url}).status_code == 400


def test_owner_header_is_trimmed_and_bounded(client: TestClient, repo: SqliteRepository) -> None:
    long_owner = {"x-owner": " " + "o" * 100}
    body = client.post("/api/v1/links", json={"url": "https://example.com/o"}, headers=long_owner).json()
    blank = client.post("/api/v1/links", json={"url": "https://example.com/b"}, headers={"x-owner": "   "}).json()
    first = repo.get_link(str(body["code"]))
    second = repo.get_link(str(blank["code"]))
    assert first is not None and first.owner == "o" * 64
    assert second is not None and second.owner == "anonymous"


# ---------------- redirect and lookup
def test_redirect_is_307_and_not_cached(client: TestClient) -> None:
    """AC-REDIRECT-1"""
    link = create(client, "https://example.com/target")
    resp = client.get(f"/{link['code']}", follow_redirects=False)
    assert resp.status_code == 307 and resp.headers["location"] == "https://example.com/target"
    assert resp.headers["cache-control"] == "no-store"


def test_unknown_code_is_404_with_request_id(client: TestClient) -> None:
    """AC-REDIRECT-2"""
    resp = client.get("/doesnotexist", headers={"x-request-id": "rid-123"}, follow_redirects=False)
    assert resp.status_code == 404 and resp.headers["x-request-id"] == "rid-123"
    assert resp.json() == {"error": {"code": "not_found", "message": "link 'doesnotexist' not found",
                                     "request_id": "rid-123"}}
    assert client.get("/api/v1/links/nope").status_code == 404


def test_get_link_details_never_include_the_stats_token(client: TestClient) -> None:
    link = create(client)
    details = client.get(f"/api/v1/links/{link['code']}").json()
    assert "stats_token" not in details and details == {k: v for k, v in link.items() if k != "stats_token"}


# ---------------- aliases and expiry
def test_custom_alias(client: TestClient) -> None:
    """AC-ALIAS-1 AC-ALIAS-2 AC-ALIAS-3"""
    assert create(client, "https://example.com/sale", custom_alias="spring-sale")["code"] == "spring-sale"
    taken = client.post("/api/v1/links", json={"url": "https://example.com/2", "custom_alias": "spring-sale"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "alias_conflict"
    for alias in ("ab", "api", "has space"):
        resp = client.post("/api/v1/links", json={"url": "https://example.com/3", "custom_alias": alias})
        assert resp.status_code == 400


def test_link_expires(client: TestClient, clock: FakeClock) -> None:
    """AC-EXPIRY-1 AC-EXPIRY-2 AC-EXPIRY-3"""
    link = create(client, "https://example.com/promo", ttl_seconds=60)
    assert str(link["expires_at"]).startswith("2026-01-15T12:01:00")
    assert client.get(f"/{link['code']}", follow_redirects=False).status_code == 307
    clock.advance(60)
    gone = client.get(f"/{link['code']}", follow_redirects=False)
    assert gone.status_code == 410 and gone.json()["error"]["code"] == "link_expired"
    for ttl in (0, 60 * 60 * 24 * 366):
        assert client.post("/api/v1/links", json={"url": "https://example.com", "ttl_seconds": ttl}).status_code == 400


# ---------------- admin
def test_admin_can_deactivate(client: TestClient) -> None:
    """AC-ADMIN-1"""
    link = create(client)
    key = {"x-api-key": "test-admin-key"}
    assert client.delete(f"/api/v1/links/{link['code']}", headers=key).status_code == 204
    assert client.get(f"/{link['code']}", follow_redirects=False).status_code == 404
    assert client.delete(f"/api/v1/links/{link['code']}", headers=key).status_code == 404


@pytest.mark.parametrize("headers", [{}, {"x-api-key": "wrong"}])
def test_admin_requires_valid_key(client: TestClient, headers: dict[str, str]) -> None:
    """AC-ADMIN-2"""
    link = create(client)
    resp = client.delete(f"/api/v1/links/{link['code']}", headers=headers)
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "unauthorized"


def test_admin_disabled_when_no_key_configured(repo: SqliteRepository, clock: FakeClock) -> None:
    client = TestClient(create_app(Settings(admin_api_key=""), repo, clock=clock))
    link = create(client)
    assert client.delete(f"/api/v1/links/{link['code']}", headers={"x-api-key": ""}).status_code == 401


# ---------------- reliability: unexpected errors and logging
class BrokenRepo(SqliteRepository):
    def get_link(self, code: str) -> None:  # type: ignore[override]
        raise RuntimeError("disk on fire")


def test_unexpected_error_returns_500_envelope_and_is_logged(
        clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(create_app(Settings(), BrokenRepo(), clock=clock), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="urlshort.api"):
        resp = client.get("/api/v1/links/abc", headers={"x-request-id": "rid-500"})
    assert resp.status_code == 500 and resp.headers["x-request-id"] == "rid-500"
    assert resp.json() == {"error": {"code": "internal_error", "message": "an unexpected error occurred",
                                     "request_id": "rid-500"}}
    assert "disk on fire" not in resp.text                                 # internals never leak to clients
    assert any("disk on fire" in r.getMessage() or (r.exc_info and "disk on fire" in str(r.exc_info[1]))
               for r in caplog.records)


def test_every_request_is_logged_with_its_request_id(client: TestClient) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("urlshort.api")
    logger.addHandler(handler)
    try:
        client.get("/healthz", headers={"x-request-id": "trace-42"})
    finally:
        logger.removeHandler(handler)
    entry = next(e for e in map(json.loads, stream.getvalue().splitlines()) if e["msg"] == "request")
    assert entry["request_id"] == "trace-42"
    assert entry["ctx"]["status"] == 200 and entry["ctx"]["path"] == "/healthz" and entry["ctx"]["duration_ms"] >= 0


# ---------------- protected analytics
def test_create_returns_stats_token_once_and_is_not_cached(client: TestClient) -> None:
    """AC-ANALYTICS-4"""
    first = client.post("/api/v1/links", json={"url": "https://example.com/tok"})
    again = client.post("/api/v1/links", json={"url": "https://example.com/tok"})
    assert first.status_code == 201 and first.json()["stats_token"] and first.headers["cache-control"] == "no-store"
    assert again.status_code == 200 and again.json()["stats_token"] is None


def test_stats_with_token(client: TestClient) -> None:
    """AC-ANALYTICS-1 AC-ANALYTICS-2 AC-ANALYTICS-3"""
    link = create(client, "https://example.com/s")
    code, token = link["code"], str(link["stats_token"])
    chrome = {"user-agent": "Mozilla/5.0 Chrome/126.0"}
    client.get(f"/{code}", headers={**chrome, "referer": "https://news.example/post/1"}, follow_redirects=False)
    client.get(f"/{code}", headers=chrome, follow_redirects=False)
    client.get(f"/{code}", headers={"user-agent": "Googlebot/2.1"}, follow_redirects=False)
    resp = client.get(f"/api/v1/links/{code}/stats", headers={"x-stats-token": token})
    stats = resp.json()
    assert resp.status_code == 200 and stats["code"] == code
    assert stats["total_clicks"] == 2 and stats["bot_clicks"] == 1
    assert stats["clicks_by_day"] == {"2026-01-15": 2} and stats["unique_visitors_by_day"] == {"2026-01-15": 1}
    assert {"host": "news.example", "clicks": 1} in stats["top_referrers"]
    assert stats["agents"] == {"chrome": 2} and stats["last_click_at"].startswith("2026-01-15T12:00:00")
    assert client.get(f"/api/v1/links/{code}").json()["click_count"] == 2


@pytest.mark.parametrize("headers", [{}, {"x-stats-token": "wrong"}, {"x-api-key": "wrong"}])
def test_stats_require_valid_credentials(client: TestClient, headers: dict[str, str]) -> None:
    """AC-ANALYTICS-4"""
    code = create(client)["code"]
    resp = client.get(f"/api/v1/links/{code}/stats", headers=headers)
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "unauthorized"


def test_stats_do_not_reveal_which_codes_exist(client: TestClient) -> None:
    """AC-ANALYTICS-4: without credentials, unknown and existing codes look the same."""
    other_token = str(create(client)["stats_token"])
    assert client.get("/api/v1/links/nope/stats", headers={"x-stats-token": other_token}).status_code == 401


def test_admin_can_read_any_stats(client: TestClient) -> None:
    code = create(client)["code"]
    admin = {"x-api-key": "test-admin-key"}
    assert client.get(f"/api/v1/links/{code}/stats", headers=admin).status_code == 200
    assert client.get("/api/v1/links/nope/stats", headers=admin).status_code == 404


def test_one_links_token_does_not_open_another(client: TestClient) -> None:
    a, b = create(client, "https://example.com/a"), create(client, "https://example.com/b")
    resp = client.get(f"/api/v1/links/{b['code']}/stats", headers={"x-stats-token": str(a["stats_token"])})
    assert resp.status_code == 401


# ---------------- rate limiting
class Mono:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def limited_app(repo: SqliteRepository, clock: FakeClock, mono: Mono) -> TestClient:
    settings = Settings(create_rate_per_minute=60, create_burst=2, redirect_rate_per_minute=60, redirect_burst=2)
    return TestClient(create_app(settings, repo, clock=clock, monotonic=mono))


def test_create_rate_limit_returns_429_with_headers(repo: SqliteRepository, clock: FakeClock) -> None:
    """AC-RATELIMIT-1"""
    mono = Mono()
    client = limited_app(repo, clock, mono)
    ok = client.post("/api/v1/links", json={"url": "https://example.com/1"})
    assert ok.status_code == 201 and ok.headers["ratelimit-limit"] == "2" and ok.headers["ratelimit-remaining"] == "1"
    assert client.post("/api/v1/links", json={"url": "https://example.com/2"}).status_code == 201
    refused = client.post("/api/v1/links", json={"url": "https://example.com/3"})
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "rate_limited"
    assert refused.headers["retry-after"] == "1" and refused.headers["ratelimit-remaining"] == "0"
    mono.t += 1.0
    assert client.post("/api/v1/links", json={"url": "https://example.com/3"}).status_code == 201


def test_invalid_requests_count_towards_the_limit(repo: SqliteRepository, clock: FakeClock) -> None:
    """AC-RATELIMIT-1: malformed requests cannot be used to probe for free."""
    client = limited_app(repo, clock, Mono())
    for _ in range(2):
        assert client.post("/api/v1/links", json={"nope": 1}).status_code == 400
    assert client.post("/api/v1/links", json={"url": "https://example.com"}).status_code == 429


def test_limits_are_per_client(repo: SqliteRepository, clock: FakeClock) -> None:
    """AC-RATELIMIT-1"""
    settings = Settings(create_burst=1)
    app = create_app(settings, repo, clock=clock, monotonic=Mono())
    alice = TestClient(app, client=("203.0.113.1", 5000))
    bob = TestClient(app, client=("203.0.113.2", 5000))
    assert alice.post("/api/v1/links", json={"url": "https://example.com/a"}).status_code == 201
    assert alice.post("/api/v1/links", json={"url": "https://example.com/b"}).status_code == 429
    assert bob.post("/api/v1/links", json={"url": "https://example.com/c"}).status_code == 201


def test_redirect_rate_limit(repo: SqliteRepository, clock: FakeClock) -> None:
    """AC-RATELIMIT-2: code scanning and click inflation are throttled."""
    client = limited_app(repo, clock, Mono())
    code = create(client)["code"]
    assert client.get(f"/{code}", follow_redirects=False).status_code == 307
    assert client.get("/scan0001", follow_redirects=False).status_code == 404     # misses count too
    refused = client.get(f"/{code}", follow_redirects=False)
    assert refused.status_code == 429 and "retry-after" in refused.headers


def test_unknown_peer_shares_one_key() -> None:
    from starlette.requests import Request as StarletteRequest

    from urlshort.api import client_key
    assert client_key(StarletteRequest({"type": "http", "client": None, "headers": []})) == "unknown"
