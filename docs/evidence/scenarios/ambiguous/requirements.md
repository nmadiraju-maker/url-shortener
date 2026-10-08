# Requirements — v0.11 - safer links, richer analytics

> Source requirement: _Make the short links safer and track more stuff about clicks. It should be fast._

## Problem statement
Provide a reliable, secure URL shortening service with analytics.

## Ambiguities & resolutions
| Term | Clarifying question | Resolution | Resolved by |
|---|---|---|---|
| safer | What threat should 'safer' address: browser-level hardening (security headers), or phishing links disguised with look-alike domains? | reject look-alike (homoglyph) domains | human clarification |
| track more | Which additional analytics dimension is needed: time of day, geography, or device? | see clicks by hour of day (UTC) | agent assumption (needs sign-off) |
| more stuff | 'More stuff' is unbounded: which measurable outcome is expected? | see clicks by hour of day (UTC) | human clarification |
| fast | What is the latency target (e.g. p99 redirect < 50 ms) and at what load? | No new work: covered by NFR-1 (p99 < 50 ms) | human clarification |

## User stories

### US-01 (Must, existing — regression only) — shorten
**As a** API client, **I want to** submit a long URL and receive a short link, **so that** I can share it easily.

| AC | Given | When | Then |
|---|---|---|---|
| AC-SHORTEN-1 | a valid http(s) URL | POST /api/v1/links is called | 201 with code, short_url and target_url |
| AC-SHORTEN-2 | the same owner already shortened the same URL (no alias/TTL) | it is submitted again | 200 with the existing code (safe retries) |
| AC-SHORTEN-3 | an invalid request (bad URL, unknown field, malformed body) | it is submitted | 400 invalid_input |

### US-02 (Must, existing — regression only) — analytics
**As a** link owner, **I want to** see click analytics for my link, **so that** I can measure engagement.

| AC | Given | When | Then |
|---|---|---|---|
| AC-ANALYTICS-1 | human clicks with referrers | GET /api/v1/links/{code}/stats | totals, clicks per day and top referrers |
| AC-ANALYTICS-2 | clicks from bots and link previews | stats are requested | bots are reported separately as bot_clicks |
| AC-ANALYTICS-3 | any click | it is recorded | no raw IP is stored; visitors are counted per day with a keyed hash |
| AC-ANALYTICS-4 | a stats request without the link's token | it is made | 401, also for codes that do not exist |

### US-03 (Must) — lookalike
**As a** security engineer, **I want to** reject look-alike (homoglyph) domains, **so that** short links cannot disguise phishing sites.

| AC | Given | When | Then |
|---|---|---|---|
| AC-LOOKALIKE-1 | a hostname mixing scripts, e.g. Latin with Cyrillic, or its punycode form | it is submitted | 400 invalid_input |
| AC-LOOKALIKE-2 | an internationalised hostname in a single script | it is submitted | it is accepted |

### US-04 (Should) — hourly
**As a** link owner, **I want to** see clicks by hour of day (UTC), **so that** I can schedule campaigns when audiences are active.

| AC | Given | When | Then |
|---|---|---|---|
| AC-HOURLY-1 | human clicks at different hours | stats are requested | clicks_by_hour maps UTC hour (00-23) to counts |

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
