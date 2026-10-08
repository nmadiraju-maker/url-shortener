# Requirements — URL shortener v0.9

> Source requirement: _Build a URL shortener core API to shorten links and redirect, with custom alias support, link expiry (ttl), click analytics, admin takedown, rate limiting, health checks, an audit trail, URL safety (SSRF), trusted load balancer proxy handling, security headers and CORS for browser origins._

## Problem statement
Provide a reliable, secure URL shortening service with analytics.

## User stories

### US-01 (Must) — shorten
**As a** API client, **I want to** submit a long URL and receive a short link, **so that** I can share it easily.

| AC | Given | When | Then |
|---|---|---|---|
| AC-SHORTEN-1 | a valid http(s) URL | POST /api/v1/links is called | 201 with code, short_url and target_url |
| AC-SHORTEN-2 | the same owner already shortened the same URL (no alias/TTL) | it is submitted again | 200 with the existing code (safe retries) |
| AC-SHORTEN-3 | an invalid request (bad URL, unknown field, malformed body) | it is submitted | 400 invalid_input |

### US-02 (Must) — redirect
**As a** end user, **I want to** open a short link and land on the target, **so that** the link works like the original.

| AC | Given | When | Then |
|---|---|---|---|
| AC-REDIRECT-1 | an active link | GET /{code} | 307 to the target with Cache-Control: no-store |
| AC-REDIRECT-2 | an unknown code | GET /{code} | 404 with the JSON error envelope and request_id |

### US-03 (Should) — alias
**As a** marketer, **I want to** choose a memorable custom alias, **so that** links are recognisable.

| AC | Given | When | Then |
|---|---|---|---|
| AC-ALIAS-1 | a free valid alias | a link is created with it | the alias is the code |
| AC-ALIAS-2 | an alias already in use | it is requested | 409 alias_conflict |
| AC-ALIAS-3 | an invalid or reserved alias | it is requested | 400 invalid_input |

### US-04 (Should) — expiry
**As a** marketer, **I want to** set an expiry on a link, **so that** campaign links stop working after the campaign.

| AC | Given | When | Then |
|---|---|---|---|
| AC-EXPIRY-1 | ttl_seconds in range | a link is created | expires_at = created_at + ttl |
| AC-EXPIRY-2 | an expired link | it is requested | 410 link_expired |
| AC-EXPIRY-3 | ttl_seconds <= 0 or above the configured maximum | a link is created | 400 invalid_input |

### US-05 (Must) — analytics
**As a** link owner, **I want to** see click analytics for my link, **so that** I can measure engagement.

| AC | Given | When | Then |
|---|---|---|---|
| AC-ANALYTICS-1 | human clicks with referrers | GET /api/v1/links/{code}/stats | totals, clicks per day and top referrers |
| AC-ANALYTICS-2 | clicks from bots and link previews | stats are requested | bots are reported separately as bot_clicks |
| AC-ANALYTICS-3 | any click | it is recorded | no raw IP is stored; visitors are counted per day with a keyed hash |
| AC-ANALYTICS-4 | a stats request without the link's token | it is made | 401, also for codes that do not exist |

### US-06 (Must) — ratelimit
**As a** operator, **I want to** limit requests per client, **so that** abuse cannot exhaust the service.

| AC | Given | When | Then |
|---|---|---|---|
| AC-RATELIMIT-1 | a client exceeding its link-creation allowance | it creates another link | 429 with Retry-After |
| AC-RATELIMIT-2 | a client scanning or hammering redirects | it exceeds the redirect allowance | 429 with Retry-After |

### US-07 (Must) — admin
**As a** trust & safety admin, **I want to** deactivate a link, **so that** abusive links can be taken down.

| AC | Given | When | Then |
|---|---|---|---|
| AC-ADMIN-1 | a valid admin key | DELETE /api/v1/links/{code} | 204 and later redirects return 404 |
| AC-ADMIN-2 | a missing or wrong key | DELETE is called | 401 unauthorized |

### US-08 (Must) — health
**As a** SRE, **I want to** probe liveness and readiness, **so that** orchestrators can route traffic safely.

| AC | Given | When | Then |
|---|---|---|---|
| AC-HEALTH-1 | the service is running | /livez and /readyz are probed | 200; /readyz is 503 when the database is down |

### US-09 (Must) — audit
**As a** compliance officer, **I want to** see a tamper-evident log of link changes, **so that** we can evidence who changed what.

| AC | Given | When | Then |
|---|---|---|---|
| AC-AUDIT-1 | links are created or deactivated | the audit trail is verified | records are hash-chained; tampering is detected |

### US-10 (Must) — safety
**As a** security engineer, **I want to** reject unsafe targets, **so that** the shortener cannot be used for SSRF or redirect chains.

| AC | Given | When | Then |
|---|---|---|---|
| AC-SAFETY-1 | a private/internal host, credentials, a blocked domain or another shortener | it is submitted | 400 invalid_input |

### US-11 (Must) — proxy
**As a** operator, **I want to** see real client addresses behind the load balancer, **so that** limits and analytics are per client.

| AC | Given | When | Then |
|---|---|---|---|
| AC-PROXY-1 | a request through a trusted proxy | it is rate-limited and counted | the real client address is used |
| AC-PROXY-2 | an untrusted peer sending X-Forwarded-For | it makes requests | the header is ignored (no spoofing) |

### US-12 (Must) — headers
**As a** security engineer, **I want to** send hardened headers on every response, **so that** browsers apply safe defaults.

| AC | Given | When | Then |
|---|---|---|---|
| AC-HEADERS-1 | any response | it is served | nosniff, Referrer-Policy, X-Robots-Tag, CSP and frame protection |

### US-13 (Should) — cors
**As a** frontend developer, **I want to** call the API from an approved web app, **so that** browsers allow it, others do not.

| AC | Given | When | Then |
|---|---|---|---|
| AC-CORS-1 | a configured origin | it sends a preflight | it is allowed; other origins are not |

## Non-functional requirements
- **NFR-1** Redirect p99 latency < 50 ms at 500 rps on one instance
- **NFR-2** No raw client IPs, tokens or credentials at rest or in logs
- **NFR-3** Every state-changing operation produces a tamper-evident audit record
- **NFR-4** Unit and functional tests with 100% line and branch coverage
- **NFR-5** Structured JSON logs with request correlation IDs on every line
- **NFR-6** Production mode refuses unsafe configuration at startup

## Out of scope
- User accounts/OAuth (API key + owner header only)
- Geo-IP analytics
- Multi-region replication
