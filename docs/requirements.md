# Requirements and traceability

_Generated 2026-10-08 by `scripts/generate_sdlc_docs.py` from `sdlc/agents/catalog.py` and the test suite._

Every acceptance criterion (AC) is traced to the tests that verify it: tests name their ACs in their docstrings, and the QA agent uses the same mapping in every orchestrator run.

| Story | Priority | Delivered | ACs | Verified by passing tests |
|---|---|---|---|---|
| US-01 shorten | Must | v0.9.0 | 3 | 3/3 |
| US-02 redirect | Must | v0.9.0 | 2 | 2/2 |
| US-03 alias | Should | v0.9.0 | 3 | 3/3 |
| US-04 expiry | Should | v0.9.0 | 3 | 3/3 |
| US-05 analytics | Must | v0.9.0 | 4 | 4/4 |
| US-06 ratelimit | Must | v0.9.0 | 2 | 2/2 |
| US-07 admin | Must | v0.9.0 | 2 | 2/2 |
| US-08 health | Must | v0.9.0 | 1 | 1/1 |
| US-09 audit | Must | v0.9.0 | 1 | 1/1 |
| US-10 safety | Must | v0.9.0 | 1 | 1/1 |
| US-11 proxy | Must | v0.9.0 | 2 | 2/2 |
| US-12 headers | Must | v0.9.0 | 1 | 1/1 |
| US-13 cors | Should | v0.9.0 | 1 | 1/1 |
| US-14 max_clicks | Must | v0.10.0 (brownfield scenario) | 3 | 3/3 |
| US-15 lookalike | Must | v0.11.0 (ambiguous scenario) | 2 | 2/2 |
| US-16 hourly | Should | v0.11.0 (ambiguous scenario) | 1 | 1/1 |

## User stories and acceptance criteria

### US-01 shorten
**As a** API client, **I want to** submit a long URL and receive a short link, **so that** I can share it easily.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-SHORTEN-1 | a valid http(s) URL | POST /api/v1/links is called | 201 with code, short_url and target_url | `test_create_returns_201_with_short_url` |
| AC-SHORTEN-2 | the same owner already shortened the same URL (no alias/TTL) | it is submitted again | 200 with the existing code (safe retries) | `test_repeat_create_is_idempotent_per_owner` |
| AC-SHORTEN-3 | an invalid request (bad URL, unknown field, malformed body) | it is submitted | 400 invalid_input | `test_invalid_requests_return_400_envelope`, `test_malformed_json_body_is_400` |

### US-02 redirect
**As a** end user, **I want to** open a short link and land on the target, **so that** the link works like the original.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-REDIRECT-1 | an active link | GET /{code} | 307 to the target with Cache-Control: no-store | `test_redirect_is_307_and_not_cached` |
| AC-REDIRECT-2 | an unknown code | GET /{code} | 404 with the JSON error envelope and request_id | `test_unknown_code_is_404_with_request_id` |

### US-03 alias
**As a** marketer, **I want to** choose a memorable custom alias, **so that** links are recognisable.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-ALIAS-1 | a free valid alias | a link is created with it | the alias is the code | `test_custom_alias` |
| AC-ALIAS-2 | an alias already in use | it is requested | 409 alias_conflict | `test_custom_alias` |
| AC-ALIAS-3 | an invalid or reserved alias | it is requested | 400 invalid_input | `test_custom_alias` |

### US-04 expiry
**As a** marketer, **I want to** set an expiry on a link, **so that** campaign links stop working after the campaign.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-EXPIRY-1 | ttl_seconds in range | a link is created | expires_at = created_at + ttl | `test_link_expires` |
| AC-EXPIRY-2 | an expired link | it is requested | 410 link_expired | `test_link_expires` |
| AC-EXPIRY-3 | ttl_seconds <= 0 or above the configured maximum | a link is created | 400 invalid_input | `test_link_expires` |

### US-05 analytics
**As a** link owner, **I want to** see click analytics for my link, **so that** I can measure engagement.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-ANALYTICS-1 | human clicks with referrers | GET /api/v1/links/{code}/stats | totals, clicks per day and top referrers | `test_stats_with_token` |
| AC-ANALYTICS-2 | clicks from bots and link previews | stats are requested | bots are reported separately as bot_clicks | `test_stats_with_token` |
| AC-ANALYTICS-3 | any click | it is recorded | no raw IP is stored; visitors are counted per day with a keyed hash | `test_stats_with_token` |
| AC-ANALYTICS-4 | a stats request without the link's token | it is made | 401, also for codes that do not exist | `test_create_returns_stats_token_once_and_is_not_cached`, `test_stats_require_valid_credentials`, `test_stats_do_not_reveal_which_codes_exist` |

### US-06 ratelimit
**As a** operator, **I want to** limit requests per client, **so that** abuse cannot exhaust the service.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-RATELIMIT-1 | a client exceeding its link-creation allowance | it creates another link | 429 with Retry-After | `test_create_rate_limit_returns_429_with_headers`, `test_invalid_requests_count_towards_the_limit`, `test_limits_are_per_client` |
| AC-RATELIMIT-2 | a client scanning or hammering redirects | it exceeds the redirect allowance | 429 with Retry-After | `test_redirect_rate_limit` |

### US-07 admin
**As a** trust & safety admin, **I want to** deactivate a link, **so that** abusive links can be taken down.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-ADMIN-1 | a valid admin key | DELETE /api/v1/links/{code} | 204 and later redirects return 404 | `test_admin_can_deactivate` |
| AC-ADMIN-2 | a missing or wrong key | DELETE is called | 401 unauthorized | `test_admin_requires_valid_key` |

### US-08 health
**As a** SRE, **I want to** probe liveness and readiness, **so that** orchestrators can route traffic safely.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-HEALTH-1 | the service is running | /livez and /readyz are probed | 200; /readyz is 503 when the database is down | `test_readyz_reflects_database_reachability` |

### US-09 audit
**As a** compliance officer, **I want to** see a tamper-evident log of link changes, **so that** we can evidence who changed what.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-AUDIT-1 | links are created or deactivated | the audit trail is verified | records are hash-chained; tampering is detected | `test_tampering_is_detected` |

### US-10 safety
**As a** security engineer, **I want to** reject unsafe targets, **so that** the shortener cannot be used for SSRF or redirect chains.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-SAFETY-1 | a private/internal host, credentials, a blocked domain or another shortener | it is submitted | 400 invalid_input | `test_unsafe_targets_are_rejected` |

### US-11 proxy
**As a** operator, **I want to** see real client addresses behind the load balancer, **so that** limits and analytics are per client.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-PROXY-1 | a request through a trusted proxy | it is rate-limited and counted | the real client address is used | `test_rate_limits_use_the_real_client_behind_a_trusted_proxy`, `test_analytics_count_real_clients_behind_proxy` |
| AC-PROXY-2 | an untrusted peer sending X-Forwarded-For | it makes requests | the header is ignored (no spoofing) | `test_spoofed_header_from_untrusted_peer_is_ignored` |

### US-12 headers
**As a** security engineer, **I want to** send hardened headers on every response, **so that** browsers apply safe defaults.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-HEADERS-1 | any response | it is served | nosniff, Referrer-Policy, X-Robots-Tag, CSP and frame protection | `test_security_headers_on_every_response`, `test_security_headers_on_redirects` |

### US-13 cors
**As a** frontend developer, **I want to** call the API from an approved web app, **so that** browsers allow it, others do not.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-CORS-1 | a configured origin | it sends a preflight | it is allowed; other origins are not | `test_cors_allows_only_configured_origins` |

### US-14 max_clicks
**As a** marketer, **I want to** cap how many times a link can be used, **so that** limited offers cannot be over-redeemed.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-MAXCLICKS-1 | a link with max_clicks=N | the (N+1)th human redirect happens | 410 link_exhausted, even under concurrency | `test_click_cap_is_exact_under_concurrency`, `test_cap_is_enforced_with_link_exhausted`, `test_cap_holds_under_concurrency` |
| AC-MAXCLICKS-2 | max_clicks < 1 or > 1,000,000 | a link is created | 400 invalid_input | `test_out_of_range_caps_are_rejected`, `test_service_validates_caps_too` |
| AC-MAXCLICKS-3 | bot traffic to a capped link | bots resolve it | bot clicks do not consume the limit | `test_bots_do_not_consume_the_cap` |

### US-15 lookalike
**As a** security engineer, **I want to** reject look-alike (homoglyph) domains, **so that** short links cannot disguise phishing sites.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-LOOKALIKE-1 | a hostname mixing scripts, e.g. Latin with Cyrillic, or its punycode form | it is submitted | 400 invalid_input | `test_mixed_script_hostnames_are_rejected` |
| AC-LOOKALIKE-2 | an internationalised hostname in a single script | it is submitted | it is accepted | `test_single_script_internationalised_names_are_accepted` |

### US-16 hourly
**As a** link owner, **I want to** see clicks by hour of day (UTC), **so that** I can schedule campaigns when audiences are active.

| AC | Given | When | Then | Tests |
|---|---|---|---|---|
| AC-HOURLY-1 | human clicks at different hours | stats are requested | clicks_by_hour maps UTC hour (00-23) to counts | `test_stats_include_clicks_by_hour`, `test_hours_are_utc_whatever_the_click_timezone` |

## Non-functional requirements
- **NFR-1** Redirect p99 latency < 50 ms at 500 rps on one instance
- **NFR-2** No raw client IPs, tokens or credentials at rest or in logs
- **NFR-3** Every state-changing operation produces a tamper-evident audit record
- **NFR-4** Unit and functional tests with 100% line and branch coverage
- **NFR-5** Structured JSON logs with request correlation IDs on every line
- **NFR-6** Production mode refuses unsafe configuration at startup
