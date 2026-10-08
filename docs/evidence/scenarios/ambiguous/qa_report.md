# QA report

`-m pytest -q -p no:cacheprovider -W ignore::DeprecationWarning --cov=urlshort --cov-branch --cov-report=json:/home/claude/fresh/url-shortener/runs/ambiguous/qa/coverage.json --cov-report=html:/home/claude/fresh/url-shortener/runs/ambiguous/qa/htmlcov --junitxml=/home/claude/fresh/url-shortener/runs/ambiguous/qa/junit.xml tests/service`

## Unit + functional test results
- Total: **312**, failures: 0, errors: 0, skipped: 0

## Code coverage (line + branch)
- Overall: **100.0%** (1092/1092 lines, 274/274 branches); threshold 100.0%

| File | Coverage | Missing lines |
|---|---|---|
| `urlshort/__init__.py` | 100.0% | - |
| `urlshort/analytics.py` | 100.0% | - |
| `urlshort/api.py` | 100.0% | - |
| `urlshort/audit.py` | 100.0% | - |
| `urlshort/codegen.py` | 100.0% | - |
| `urlshort/config.py` | 100.0% | - |
| `urlshort/errors.py` | 100.0% | - |
| `urlshort/logging_setup.py` | 100.0% | - |
| `urlshort/main.py` | 100.0% | - |
| `urlshort/models.py` | 100.0% | - |
| `urlshort/ratelimit.py` | 100.0% | - |
| `urlshort/service.py` | 100.0% | - |
| `urlshort/storage.py` | 100.0% | - |
| `urlshort/validation.py` | 100.0% | - |
| `urlshort/web/__init__.py` | 100.0% | - |
| `urlshort/web/clientip.py` | 100.0% | - |
| `urlshort/web/context.py` | 100.0% | - |
| `urlshort/web/middleware.py` | 100.0% | - |
| `urlshort/web/routes_links.py` | 100.0% | - |
| `urlshort/web/routes_ops.py` | 100.0% | - |
| `urlshort/web/routes_redirect.py` | 100.0% | - |

Below threshold: none

## Functional coverage (acceptance criteria → passing tests)
- **10/10 ACs (100.0%)**

| AC | Verified by |
|---|---|
| AC-SHORTEN-1 | test_create_returns_201_with_short_url |
| AC-SHORTEN-2 | test_repeat_create_is_idempotent_per_owner |
| AC-SHORTEN-3 | test_invalid_requests_return_400_envelope, test_malformed_json_body_is_400 |
| AC-ANALYTICS-1 | test_stats_with_token |
| AC-ANALYTICS-2 | test_stats_with_token |
| AC-ANALYTICS-3 | test_stats_with_token |
| AC-ANALYTICS-4 | test_create_returns_stats_token_once_and_is_not_cached, test_stats_do_not_reveal_which_codes_exist, test_stats_require_valid_credentials |
| AC-LOOKALIKE-1 | test_mixed_script_hostnames_are_rejected |
| AC-LOOKALIKE-2 | test_single_script_internationalised_names_are_accepted |
| AC-HOURLY-1 | test_hours_are_utc_whatever_the_click_timezone, test_stats_include_clicks_by_hour |
