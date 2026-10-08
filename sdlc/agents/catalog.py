"""Domain knowledge used by the offline (deterministic) Requirements, Design and Impact agents.

Each feature lists its user story, acceptance criteria (Given / When / Then), the API endpoints and
tables it touches, and the code concepts impact analysis searches for. AC IDs are
"AC-<ac_prefix>-<n>"; the service's tests carry the same IDs in their docstrings, which is how the QA
agent traces every acceptance criterion to a passing test.

The last three features are not built yet: they are what the scenarios deliver through the orchestrator.
In LLM mode the same structure is produced by a model and validated against this schema.
"""

from __future__ import annotations

from typing import Any

FEATURES: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------ built
    "shorten": {
        "ac_prefix": "SHORTEN", "priority": "Must", "keywords": ["shorten", "short link", "short url", "core api"],
        "story": ("API client", "submit a long URL and receive a short link", "I can share it easily"),
        "ac": [("a valid http(s) URL", "POST /api/v1/links is called", "201 with code, short_url and target_url"),
               ("the same owner already shortened the same URL (no alias/TTL)", "it is submitted again",
                "200 with the existing code (safe retries)"),
               ("an invalid request (bad URL, unknown field, malformed body)", "it is submitted", "400 invalid_input")],
        "api": ["POST /api/v1/links"], "tables": ["links"], "concepts": ["shorten", "create_link"]},
    "redirect": {
        "ac_prefix": "REDIRECT", "priority": "Must", "keywords": ["redirect", "resolve", "core api"],
        "story": ("end user", "open a short link and land on the target", "the link works like the original"),
        "ac": [("an active link", "GET /{code}", "307 to the target with Cache-Control: no-store"),
               ("an unknown code", "GET /{code}", "404 with the JSON error envelope and request_id")],
        "api": ["GET /{code}"], "tables": ["links", "clicks"], "concepts": ["resolve", "redirect"]},
    "alias": {
        "ac_prefix": "ALIAS", "priority": "Should", "keywords": ["alias", "custom", "vanity"],
        "story": ("marketer", "choose a memorable custom alias", "links are recognisable"),
        "ac": [("a free valid alias", "a link is created with it", "the alias is the code"),
               ("an alias already in use", "it is requested", "409 alias_conflict"),
               ("an invalid or reserved alias", "it is requested", "400 invalid_input")],
        "api": ["POST /api/v1/links"], "tables": ["links"], "concepts": ["validate_alias"]},
    "expiry": {
        "ac_prefix": "EXPIRY", "priority": "Should", "keywords": ["expir", "ttl", "time-limited"],
        "story": ("marketer", "set an expiry on a link", "campaign links stop working after the campaign"),
        "ac": [("ttl_seconds in range", "a link is created", "expires_at = created_at + ttl"),
               ("an expired link", "it is requested", "410 link_expired"),
               ("ttl_seconds <= 0 or above the configured maximum", "a link is created", "400 invalid_input")],
        "api": ["POST /api/v1/links", "GET /{code}"], "tables": ["links"], "concepts": ["validate_ttl", "LinkExpired"]},
    "analytics": {
        "ac_prefix": "ANALYTICS", "priority": "Must", "keywords": ["analytic", "stats", "click", "track"],
        "story": ("link owner", "see click analytics for my link", "I can measure engagement"),
        "ac": [("human clicks with referrers", "GET /api/v1/links/{code}/stats", "totals, clicks per day and top referrers"),
               ("clicks from bots and link previews", "stats are requested", "bots are reported separately as bot_clicks"),
               ("any click", "it is recorded", "no raw IP is stored; visitors are counted per day with a keyed hash"),
               ("a stats request without the link's token", "it is made", "401, also for codes that do not exist")],
        "api": ["GET /api/v1/links/{code}/stats"], "tables": ["clicks"], "concepts": ["summarise", "stats"]},
    "ratelimit": {
        "ac_prefix": "RATELIMIT", "priority": "Must", "keywords": ["rate limit", "abuse", "throttl", "reliab"],
        "story": ("operator", "limit requests per client", "abuse cannot exhaust the service"),
        "ac": [("a client exceeding its link-creation allowance", "it creates another link", "429 with Retry-After"),
               ("a client scanning or hammering redirects", "it exceeds the redirect allowance", "429 with Retry-After")],
        "api": ["POST /api/v1/links", "GET /{code}"], "tables": [], "concepts": ["GcraLimiter", "enforce"]},
    "admin": {
        "ac_prefix": "ADMIN", "priority": "Must", "keywords": ["delete", "disable", "deactivat", "admin", "takedown"],
        "story": ("trust & safety admin", "deactivate a link", "abusive links can be taken down"),
        "ac": [("a valid admin key", "DELETE /api/v1/links/{code}", "204 and later redirects return 404"),
               ("a missing or wrong key", "DELETE is called", "401 unauthorized")],
        "api": ["DELETE /api/v1/links/{code}"], "tables": ["links"], "concepts": ["deactivate", "is_admin"]},
    "health": {
        "ac_prefix": "HEALTH", "priority": "Must", "keywords": ["health", "readiness", "liveness", "reliab", "monitor"],
        "story": ("SRE", "probe liveness and readiness", "orchestrators can route traffic safely"),
        "ac": [("the service is running", "/livez and /readyz are probed", "200; /readyz is 503 when the database is down")],
        "api": ["GET /livez", "GET /readyz"], "tables": [], "concepts": ["livez", "readyz", "ping"]},
    "audit": {
        "ac_prefix": "AUDIT", "priority": "Must", "keywords": ["audit", "compliance", "traceab"],
        "story": ("compliance officer", "see a tamper-evident log of link changes", "we can evidence who changed what"),
        "ac": [("links are created or deactivated", "the audit trail is verified", "records are hash-chained; tampering is detected")],
        "api": [], "tables": ["audit_log"], "concepts": ["AuditTrail"]},
    "safety": {
        "ac_prefix": "SAFETY", "priority": "Must", "keywords": ["ssrf", "malicious", "phishing", "url safety", "reliab"],
        "story": ("security engineer", "reject unsafe targets", "the shortener cannot be used for SSRF or redirect chains"),
        "ac": [("a private/internal host, credentials, a blocked domain or another shortener", "it is submitted",
                "400 invalid_input")],
        "api": ["POST /api/v1/links"], "tables": [], "concepts": ["validate_url"]},
    "proxy": {
        "ac_prefix": "PROXY", "priority": "Must", "keywords": ["load balancer", "proxy", "x-forwarded-for"],
        "story": ("operator", "see real client addresses behind the load balancer", "limits and analytics are per client"),
        "ac": [("a request through a trusted proxy", "it is rate-limited and counted", "the real client address is used"),
               ("an untrusted peer sending X-Forwarded-For", "it makes requests", "the header is ignored (no spoofing)")],
        "api": [], "tables": [], "concepts": ["TrustedProxies", "client_ip"]},
    "headers": {
        "ac_prefix": "HEADERS", "priority": "Must", "keywords": ["security headers", "noindex", "hsts"],
        "story": ("security engineer", "send hardened headers on every response", "browsers apply safe defaults"),
        "ac": [("any response", "it is served", "nosniff, Referrer-Policy, X-Robots-Tag, CSP and frame protection")],
        "api": [], "tables": [], "concepts": ["security_headers"]},
    "cors": {
        "ac_prefix": "CORS", "priority": "Should", "keywords": ["cors", "browser origin"],
        "story": ("frontend developer", "call the API from an approved web app", "browsers allow it, others do not"),
        "ac": [("a configured origin", "it sends a preflight", "it is allowed; other origins are not")],
        "api": [], "tables": [], "concepts": ["install_middleware"]},
    # ------------------------------------------------------------------ to be delivered by scenarios
    "max_clicks": {
        "ac_prefix": "MAXCLICKS", "priority": "Must", "keywords": ["max click", "click limit", "cap clicks", "max_clicks"],
        "story": ("marketer", "cap how many times a link can be used", "limited offers cannot be over-redeemed"),
        "ac": [("a link with max_clicks=N", "the (N+1)th human redirect happens", "410 link_exhausted, even under concurrency"),
               ("max_clicks < 1 or > 1,000,000", "a link is created", "400 invalid_input"),
               ("bot traffic to a capped link", "bots resolve it", "bot clicks do not consume the limit")],
        "api": ["POST /api/v1/links", "GET /{code}"], "tables": ["links"], "schema_change": "links.max_clicks INTEGER",
        "concepts": ["click_count", "record_click", "resolve", "Link", "CreateLinkRequest", "LinkResponse", "shorten",
                     "validate_ttl", "LinkExpired", "to_response"]},
    "lookalike": {
        "ac_prefix": "LOOKALIKE", "priority": "Must", "keywords": ["look-alike", "lookalike", "homoglyph", "idn"],
        "story": ("security engineer", "reject look-alike (homoglyph) domains", "short links cannot disguise phishing sites"),
        "ac": [("a hostname mixing scripts, e.g. Latin with Cyrillic, or its punycode form", "it is submitted", "400 invalid_input"),
               ("an internationalised hostname in a single script", "it is submitted", "it is accepted")],
        "api": ["POST /api/v1/links"], "tables": [], "concepts": ["validate_url"]},
    "hourly": {
        "ac_prefix": "HOURLY", "priority": "Should", "keywords": ["hourly", "per hour", "time of day"],
        "story": ("link owner", "see clicks by hour of day (UTC)", "I can schedule campaigns when audiences are active"),
        "ac": [("human clicks at different hours", "stats are requested", "clicks_by_hour maps UTC hour (00-23) to counts")],
        "api": ["GET /api/v1/links/{code}/stats"], "tables": [], "concepts": ["summarise", "StatsResponse"]},
}

# Vague phrases -> clarifying question + candidate interpretations (the first is the default assumption).
VAGUE_TERMS: dict[str, dict[str, Any]] = {
    "safer": {"question": "What threat should 'safer' address: browser-level hardening (security headers), or "
                          "phishing links disguised with look-alike domains?",
              "options": ["headers", "lookalike"]},
    "track more": {"question": "Which additional analytics dimension is needed: time of day, geography, or device?",
                   "options": ["hourly"]},
    "more stuff": {"question": "'More stuff' is unbounded: which measurable outcome is expected?", "options": []},
    "fast": {"question": "What is the latency target (e.g. p99 redirect < 50 ms) and at what load?", "options": []},
    "better": {"question": "Better by which metric?", "options": []},
    "scalable": {"question": "What scale: links per day, redirects per second, retention period?", "options": []},
    "asap": {"question": "Is there a hard date? What can be cut to meet it?", "options": []},
}

NFRS = [
    {"id": "NFR-1", "text": "Redirect p99 latency < 50 ms at 500 rps on one instance"},
    {"id": "NFR-2", "text": "No raw client IPs, tokens or credentials at rest or in logs"},
    {"id": "NFR-3", "text": "Every state-changing operation produces a tamper-evident audit record"},
    {"id": "NFR-4", "text": "Unit and functional tests with 100% line and branch coverage"},
    {"id": "NFR-5", "text": "Structured JSON logs with request correlation IDs on every line"},
    {"id": "NFR-6", "text": "Production mode refuses unsafe configuration at startup"},
]
