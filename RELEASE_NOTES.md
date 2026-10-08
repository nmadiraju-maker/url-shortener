# Release v0.10.0

## Stories delivered
- US-01 open a short link and land on the target (AC-REDIRECT-1, AC-REDIRECT-2)
- US-02 see click analytics for my link (AC-ANALYTICS-1, AC-ANALYTICS-2, AC-ANALYTICS-3, AC-ANALYTICS-4)
- US-03 cap how many times a link can be used (AC-MAXCLICKS-1, AC-MAXCLICKS-2, AC-MAXCLICKS-3)

## Quality
- 301 tests passing; coverage 100.0%; AC coverage 100.0%

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
