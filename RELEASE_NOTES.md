# Release v0.11.0

## Stories delivered
- US-01 submit a long URL and receive a short link (AC-SHORTEN-1, AC-SHORTEN-2, AC-SHORTEN-3)
- US-02 see click analytics for my link (AC-ANALYTICS-1, AC-ANALYTICS-2, AC-ANALYTICS-3, AC-ANALYTICS-4)
- US-03 reject look-alike (homoglyph) domains (AC-LOOKALIKE-1, AC-LOOKALIKE-2)
- US-04 see clicks by hour of day (UTC) (AC-HOURLY-1)

## Quality
- 312 tests passing; coverage 100.0%; AC coverage 100.0%

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
