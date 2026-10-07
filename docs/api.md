# API

Interactive documentation is served at `/docs` (OpenAPI at `/openapi.json`).

| Method & path | Purpose | Success | Errors |
|---|---|---|---|
| `POST /api/v1/links` | Create a short link | 201 created; 200 if an identical permanent link is reused | 400, 409 |
| `GET /{code}` | Redirect to the target | 307 with `Cache-Control: no-store` | 404, 410 |
| `GET /api/v1/links/{code}` | Link details | 200 | 404 |
| `GET /api/v1/links/{code}/stats` | Click analytics (`X-Stats-Token`, or admin `X-API-Key`) | 200 | 401, 404 |
| `DELETE /api/v1/links/{code}` | Deactivate a link (admin, `X-API-Key`) | 204 | 401, 404 |
| `GET /livez` (alias `/healthz`) | Liveness: restart the container if this fails | 200 | |
| `GET /readyz` | Readiness (database reachable) | 200 | 503 |

## Create a link

```bash
curl -s -X POST localhost:8000/api/v1/links -H 'content-type: application/json' \
  -H 'x-owner: team-a' -d '{"url": "https://example.com/spring", "custom_alias": "spring-sale", "ttl_seconds": 86400}'
```

| Field | Required | Rules |
|---|---|---|
| `url` | yes | http(s) only; no credentials; no private/internal hosts; no other shorteners; max length configurable |
| `custom_alias` | no | 3-32 letters, digits, `-`, `_`; service paths are reserved |
| `ttl_seconds` | no | 1 second up to the configured maximum (default 1 year) |

Unknown fields are rejected. Without an alias or TTL, repeating the same URL for the same owner returns the
existing link (200) instead of creating a duplicate, so client retries are safe.

`X-Owner` identifies the creating team. It is **not authenticated yet** (owner API keys are planned); treat it
as a label, not a security boundary.

## Stats tokens and analytics

Creating a link returns a `stats_token` **once**. Only its SHA-256 hash is stored, so it cannot be shown again;
a repeated (idempotent) create returns `stats_token: null`. Send it as `X-Stats-Token`:

```bash
curl -s localhost:8000/api/v1/links/abc1234/stats -H 'x-stats-token: <token from create>'
```

Without a valid token (or the admin key) the response is always 401, including for codes that do not exist,
so the endpoint cannot be used to discover which codes are in use. Links created before tokens existed are
readable by admins only. Owner API keys (planned) will replace per-link tokens for teams.

| Field | Meaning |
|---|---|
| `total_clicks` | Human clicks (bots excluded) |
| `bot_clicks` | Clicks from crawlers, link previews and scripts, by User-Agent (a heuristic: easy to spoof) |
| `clicks_by_day` | Human clicks per UTC day |
| `unique_visitors_by_day` | Distinct visitors per UTC day |
| `top_referrers` | Top 5 referring domains; `direct` when no referrer was sent |
| `agents` | Human clicks per browser family |
| `last_click_at` | Time of the latest human click |

**Privacy:** raw IP addresses are never stored or logged. Each click stores a 64-bit keyed hash of the IP
(HMAC-SHA256) under a key that changes every UTC day, derived from the `URLSHORT_IP_SALT` secret. A visitor
therefore cannot be followed across days, which is also why unique visitors are reported per day. Referrers
keep only the domain, never the path or query string.

## Security headers

Every response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`
and `X-Robots-Tag: noindex, nofollow`. API responses and redirects also carry
`Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` and `X-Frame-Options: DENY` (not the
interactive docs page, which loads its assets from a CDN). When `base_url` is https,
`Strict-Transport-Security` is added (`[http] hsts_max_age`, 0 disables). `/docs`, `/redoc` and
`/openapi.json` can be hidden with `[http] expose_docs = false`.

## CORS

Off by default. List browser origins in `[http] cors_allow_origins` (exact `scheme://host[:port]`, or `*`).
Credentials are never allowed; `X-Request-ID`, `Retry-After` and the `RateLimit-*` headers are exposed to
browser code.

## Behind a load balancer

Set `[http] trusted_proxies` to the CIDR ranges of your own proxies. `X-Forwarded-For` is then read right to
left, skipping trusted proxies, and the first untrusted address is used as the client for rate limits and
analytics. Requests not arriving directly from a trusted proxy have the header ignored, so clients cannot
spoof their address. Entries with ports or other malformed values are not trusted.

## Rate limits

Limits are per client (currently the connecting IP address):

| Applies to | Default steady rate | Default burst |
|---|---|---|
| `POST /api/v1/links` (invalid requests count too) | 60 / minute | 10 |
| `GET /{code}` (hits and misses) | 600 / minute | 100 |

Responses to link creation carry `RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset` (seconds until
the allowance is full again), so clients can slow down before being refused. A refused request returns 429
with `Retry-After` (seconds). Limits are configurable under `[ratelimit]` (see `config/urlshort.example.toml`).

The limiter uses GCRA and is in-memory per process, so each instance enforces its own limit; a shared Redis
backend using the same algorithm is planned for multi-instance deployments. Behind a load balancer, configure
`trusted_proxies` (see above), otherwise every request appears to come from the proxy and shares one limit.

## Errors

Every error, including unexpected ones, has the same shape:

```json
{"error": {"code": "link_expired", "message": "link 'abc1234' has expired", "request_id": "648c69..."}}
```

`code` is stable for programs to switch on; `request_id` matches the `X-Request-ID` response header and the
service logs. Send your own `X-Request-ID` to correlate across systems.

| Status | `code` |
|---|---|
| 400 | `invalid_input` |
| 401 | `unauthorized` |
| 404 | `not_found` |
| 409 | `alias_conflict` |
| 410 | `link_expired` |
| 429 | `rate_limited` (with `Retry-After`) |
| 500 | `internal_error` (details are logged, never returned) |
| 503 | `code_generation_failed` |
