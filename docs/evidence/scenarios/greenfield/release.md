# Release v0.9.0

## Stories delivered
- US-01 submit a long URL and receive a short link (AC-SHORTEN-1, AC-SHORTEN-2, AC-SHORTEN-3)
- US-02 open a short link and land on the target (AC-REDIRECT-1, AC-REDIRECT-2)
- US-03 choose a memorable custom alias (AC-ALIAS-1, AC-ALIAS-2, AC-ALIAS-3)
- US-04 set an expiry on a link (AC-EXPIRY-1, AC-EXPIRY-2, AC-EXPIRY-3)
- US-05 see click analytics for my link (AC-ANALYTICS-1, AC-ANALYTICS-2, AC-ANALYTICS-3, AC-ANALYTICS-4)
- US-06 limit requests per client (AC-RATELIMIT-1, AC-RATELIMIT-2)
- US-07 deactivate a link (AC-ADMIN-1, AC-ADMIN-2)
- US-08 probe liveness and readiness (AC-HEALTH-1)
- US-09 see a tamper-evident log of link changes (AC-AUDIT-1)
- US-10 reject unsafe targets (AC-SAFETY-1)
- US-11 see real client addresses behind the load balancer (AC-PROXY-1, AC-PROXY-2)
- US-12 send hardened headers on every response (AC-HEADERS-1)
- US-13 call the API from an approved web app (AC-CORS-1)

## Quality
- 289 tests passing; coverage 100.0%; AC coverage 100.0%

## Readiness checklist
- [x] all_artifacts_present
- [x] review_approved
- [x] tests_green
- [x] coverage_target_met
- [x] acceptance_criteria_verified
- [x] security_clean
- [x] audit_chain_intact

## Rollback
Redeploy previous tag; schema changes are additive (expand-only) so the previous version runs against the new schema.
