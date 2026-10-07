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


def test_requests_without_a_peer_share_one_rate_limit_key(repo: SqliteRepository) -> None:
    from starlette.requests import Request as StarletteRequest

    from urlshort.ratelimit import Decision
    from urlshort.service import ShortenerService
    from urlshort.web.clientip import TrustedProxies
    from urlshort.web.context import AppContext

    class RecordingLimiter:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def acquire(self, key: str) -> Decision:
            self.keys.append(key)
            return Decision(allowed=True, limit=1, remaining=0, reset_after=1.0, retry_after=0.0)

    limiter = RecordingLimiter()
    ctx = AppContext(settings=Settings(), repository=repo, service=ShortenerService(repo, Settings()),
                     create_limiter=limiter, redirect_limiter=limiter, proxies=TrustedProxies())
    ctx.enforce(limiter, StarletteRequest({"type": "http", "client": None, "headers": []}), "create")
    assert limiter.keys == ["unknown"]


# ---------------- logging behaviour through the API
def capture(logger_name: str) -> tuple[io.StringIO, logging.Handler]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logging.getLogger(logger_name).addHandler(handler)
    return stream, handler


def test_successful_requests_can_be_sampled_but_errors_never_are(repo: SqliteRepository, clock: FakeClock) -> None:
    client = TestClient(create_app(Settings(log_request_sample_rate=0.0), repo, clock=clock))
    stream, handler = capture("urlshort.api")
    try:
        client.get("/healthz")
        client.get("/nope-404")
    finally:
        logging.getLogger("urlshort.api").removeHandler(handler)
    statuses = [json.loads(line)["ctx"]["status"] for line in stream.getvalue().splitlines()
                if json.loads(line)["msg"] == "request"]
    assert statuses == [404]


def test_startup_summary_masks_secrets_and_warns_on_default_salt(repo: SqliteRepository) -> None:
    stream, handler = capture("urlshort.api")
    try:
        create_app(Settings(admin_api_key="super-secret-key"), repo)
        create_app(Settings(ip_hash_salt="real-salt"), repo)
    finally:
        logging.getLogger("urlshort.api").removeHandler(handler)
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    summaries = [x["ctx"]["settings"] for x in lines if x["msg"] == "service configured"]
    assert summaries[0]["admin_api_key"] == "set" and "super-secret-key" not in stream.getvalue()
    warnings = [x["msg"] for x in lines if x["level"] == "WARNING"]
    assert len(warnings) == 1 and "URLSHORT_IP_SALT" in warnings[0]                  # only the default-salt app
    assert any("admin endpoints are disabled" in x["msg"] for x in lines)            # second app has no key


# ---------------- API hardening: proxies, headers, CORS, docs, liveness
PROXY = ("10.0.0.5", 4000)


def proxied_app(repo: SqliteRepository, clock: FakeClock, **settings: object) -> TestClient:
    app = create_app(Settings(trusted_proxies=frozenset({"10.0.0.0/8"}), **settings), repo,  # type: ignore[arg-type]
                     clock=clock, monotonic=Mono())
    return TestClient(app, client=PROXY)


def test_rate_limits_use_the_real_client_behind_a_trusted_proxy(repo: SqliteRepository, clock: FakeClock) -> None:
    """AC-PROXY-1"""
    client = proxied_app(repo, clock, create_burst=1)
    def post(xff: str) -> int:
        return client.post("/api/v1/links", json={"url": "https://example.com/p"},
                           headers={"x-forwarded-for": xff}).status_code
    assert post("203.0.113.1") == 201
    assert post("203.0.113.2") in (200, 201)                 # a different client, not limited
    assert post("203.0.113.1") == 429                         # the same client again


def test_spoofed_header_from_untrusted_peer_is_ignored(repo: SqliteRepository, clock: FakeClock) -> None:
    """AC-PROXY-2: a client cannot dodge its limit by inventing X-Forwarded-For values."""
    app = create_app(Settings(trusted_proxies=frozenset({"10.0.0.0/8"}), create_burst=1), repo, clock=clock,
                     monotonic=Mono())
    attacker = TestClient(app, client=("203.0.113.66", 4000))
    def post(path: str, fake_ip: str) -> int:
        return attacker.post("/api/v1/links", json={"url": f"https://example.com/{path}"},
                             headers={"x-forwarded-for": fake_ip}).status_code
    assert (post("a", "1.1.1.1"), post("b", "2.2.2.2")) == (201, 429)


def test_analytics_count_real_clients_behind_proxy(repo: SqliteRepository, clock: FakeClock) -> None:
    """AC-PROXY-1"""
    client = proxied_app(repo, clock)
    link = create(client)
    for xff in ("203.0.113.1", "203.0.113.2", "203.0.113.1"):
        client.get(f"/{link['code']}", headers={"x-forwarded-for": xff, "user-agent": "Mozilla/5.0 Chrome/126"},
                   follow_redirects=False)
    stats = client.get(f"/api/v1/links/{link['code']}/stats", headers={"x-stats-token": str(link["stats_token"])})
    assert stats.json()["unique_visitors_by_day"] == {"2026-01-15": 2}


@pytest.mark.parametrize("path", ["/livez", "/api/v1/links/nope", "/nope-404"])
def test_security_headers_on_every_response(client: TestClient, path: str) -> None:
    """AC-HEADERS-1"""
    h = client.get(path, follow_redirects=False).headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin" and h["x-robots-tag"] == "noindex, nofollow"


def test_security_headers_on_redirects(client: TestClient) -> None:
    """AC-HEADERS-1"""
    link = create(client)
    h = client.get(f"/{link['code']}", follow_redirects=False).headers
    assert h["x-robots-tag"] == "noindex, nofollow" and h["cache-control"] == "no-store"   # route's own value kept


def test_docs_page_can_load_its_assets(client: TestClient) -> None:
    h = client.get("/docs").headers
    assert "content-security-policy" not in h and h["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("base_url,max_age,expected", [
    ("https://sho.rt", 31536000, "max-age=31536000; includeSubDomains"),
    ("https://sho.rt", 0, None),
    ("http://localhost:8000", 31536000, None),               # HSTS over plain http is meaningless
])
def test_hsts(repo: SqliteRepository, clock: FakeClock, base_url: str, max_age: int, expected: str | None) -> None:
    client = TestClient(create_app(Settings(base_url=base_url, hsts_max_age=max_age), repo, clock=clock))
    assert client.get("/livez").headers.get("strict-transport-security") == expected


def test_cors_off_by_default(client: TestClient) -> None:
    resp = client.options("/api/v1/links", headers={"origin": "https://app.example",
                                                    "access-control-request-method": "POST"})
    assert "access-control-allow-origin" not in resp.headers


def test_cors_allows_only_configured_origins(repo: SqliteRepository, clock: FakeClock) -> None:
    """AC-CORS-1"""
    client = TestClient(create_app(Settings(cors_allow_origins=frozenset({"https://app.example"})), repo, clock=clock))
    preflight = {"access-control-request-method": "POST",
                 "access-control-request-headers": "content-type,x-stats-token"}
    ok = client.options("/api/v1/links", headers={"origin": "https://app.example", **preflight})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "https://app.example"
    assert ok.headers["x-request-id"]                                    # preflights pass through our middleware too
    denied = client.options("/api/v1/links", headers={"origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in denied.headers
    real = client.post("/api/v1/links", json={"url": "https://example.com/c"}, headers={"origin": "https://app.example"})
    assert "ratelimit-remaining" in real.headers["access-control-expose-headers"].lower()


def test_docs_can_be_hidden(repo: SqliteRepository, clock: FakeClock) -> None:
    hidden = TestClient(create_app(Settings(expose_docs=False), repo, clock=clock))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert hidden.get(path).status_code == 404
    shown = TestClient(create_app(Settings(), repo, clock=clock))
    assert shown.get("/openapi.json").status_code == 200


def test_livez_and_healthz_alias(client: TestClient) -> None:
    assert client.get("/livez").json() == client.get("/healthz").json() == {"status": "ok", "version": "0.1.0"}


def test_startup_logs_trusted_proxies(repo: SqliteRepository) -> None:
    stream, handler = capture("urlshort.api")
    try:
        create_app(Settings(trusted_proxies=frozenset({"10.0.0.0/8"})), repo)
    finally:
        logging.getLogger("urlshort.api").removeHandler(handler)
    assert any(json.loads(x).get("ctx", {}).get("trusted_proxies") == ["10.0.0.0/8"]
               for x in stream.getvalue().splitlines())
