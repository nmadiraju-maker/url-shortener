# QA report

`-m pytest -q -p no:cacheprovider -W ignore::DeprecationWarning --cov=urlshort --cov-branch --cov-report=json:/home/claude/fresh/url-shortener/runs/brownfield/qa/coverage.json --cov-report=html:/home/claude/fresh/url-shortener/runs/brownfield/qa/htmlcov --junitxml=/home/claude/fresh/url-shortener/runs/brownfield/qa/junit.xml tests/service`

## Unit + functional test results
- Total: **301**, failures: 0, errors: 0, skipped: 0

## Code coverage (line + branch)
- Overall: **100.0%** (1069/1069 lines, 262/262 branches); threshold 100.0%

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
- **9/9 ACs (100.0%)**

| AC | Verified by |
|---|---|
| AC-REDIRECT-1 | test_redirect_is_307_and_not_cached |
| AC-REDIRECT-2 | test_unknown_code_is_404_with_request_id |
| AC-ANALYTICS-1 | test_stats_with_token |
| AC-ANALYTICS-2 | test_stats_with_token |
| AC-ANALYTICS-3 | test_stats_with_token |
| AC-ANALYTICS-4 | test_create_returns_stats_token_once_and_is_not_cached, test_stats_do_not_reveal_which_codes_exist, test_stats_require_valid_credentials |
| AC-MAXCLICKS-1 | test_cap_holds_under_concurrency, test_cap_is_enforced_with_link_exhausted |
| AC-MAXCLICKS-2 | test_out_of_range_caps_are_rejected, test_service_validates_caps_too |
| AC-MAXCLICKS-3 | test_bots_do_not_consume_the_cap |
