# Requirements — v0.10 - click caps

> Source requirement: _Marketing needs an optional max_clicks limit per link: once the cap is reached the link must stop redirecting, even under heavy concurrent traffic._

## Problem statement
Provide a reliable, secure URL shortening service with analytics.

## User stories

### US-01 (Must, existing — regression only) — redirect
**As a** end user, **I want to** open a short link and land on the target, **so that** the link works like the original.

| AC | Given | When | Then |
|---|---|---|---|
| AC-REDIRECT-1 | an active link | GET /{code} | 307 to the target with Cache-Control: no-store |
| AC-REDIRECT-2 | an unknown code | GET /{code} | 404 with the JSON error envelope and request_id |

### US-02 (Must, existing — regression only) — analytics
**As a** link owner, **I want to** see click analytics for my link, **so that** I can measure engagement.

| AC | Given | When | Then |
|---|---|---|---|
| AC-ANALYTICS-1 | human clicks with referrers | GET /api/v1/links/{code}/stats | totals, clicks per day and top referrers |
| AC-ANALYTICS-2 | clicks from bots and link previews | stats are requested | bots are reported separately as bot_clicks |
| AC-ANALYTICS-3 | any click | it is recorded | no raw IP is stored; visitors are counted per day with a keyed hash |
| AC-ANALYTICS-4 | a stats request without the link's token | it is made | 401, also for codes that do not exist |

### US-03 (Must) — max_clicks
**As a** marketer, **I want to** cap how many times a link can be used, **so that** limited offers cannot be over-redeemed.

| AC | Given | When | Then |
|---|---|---|---|
| AC-MAXCLICKS-1 | a link with max_clicks=N | the (N+1)th human redirect happens | 410 link_exhausted, even under concurrency |
| AC-MAXCLICKS-2 | max_clicks < 1 or > 1,000,000 | a link is created | 400 invalid_input |
| AC-MAXCLICKS-3 | bot traffic to a capped link | bots resolve it | bot clicks do not consume the limit |

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
