# QA report

`-m pytest -q -p no:cacheprovider -W ignore::DeprecationWarning --cov=urlshort --cov-branch --cov-report=json:/home/claude/fresh/url-shortener/runs/greenfield/qa/coverage.json --cov-report=html:/home/claude/fresh/url-shortener/runs/greenfield/qa/htmlcov --junitxml=/home/claude/fresh/url-shortener/runs/greenfield/qa/junit.xml tests/service`

## Unit + functional test results
- Total: **289**, failures: 0, errors: 0, skipped: 0

## Code coverage (line + branch)
- Overall: **100.0%** (1031/1031 lines, 250/250 branches); threshold 100.0%

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
- **26/26 ACs (100.0%)**

| AC | Verified by |
|---|---|
| AC-SHORTEN-1 | test_create_returns_201_with_short_url |
| AC-SHORTEN-2 | test_repeat_create_is_idempotent_per_owner |
| AC-SHORTEN-3 | test_invalid_requests_return_400_envelope, test_malformed_json_body_is_400 |
| AC-REDIRECT-1 | test_redirect_is_307_and_not_cached |
| AC-REDIRECT-2 | test_unknown_code_is_404_with_request_id |
| AC-ALIAS-1 | test_custom_alias |
| AC-ALIAS-2 | test_custom_alias |
| AC-ALIAS-3 | test_custom_alias |
| AC-EXPIRY-1 | test_link_expires |
| AC-EXPIRY-2 | test_link_expires |
| AC-EXPIRY-3 | test_link_expires |
| AC-ANALYTICS-1 | test_stats_with_token |
| AC-ANALYTICS-2 | test_stats_with_token |
| AC-ANALYTICS-3 | test_stats_with_token |
| AC-ANALYTICS-4 | test_create_returns_stats_token_once_and_is_not_cached, test_stats_do_not_reveal_which_codes_exist, test_stats_require_valid_credentials |
| AC-RATELIMIT-1 | test_create_rate_limit_returns_429_with_headers, test_invalid_requests_count_towards_the_limit, test_limits_are_per_client |
| AC-RATELIMIT-2 | test_redirect_rate_limit |
| AC-ADMIN-1 | test_admin_can_deactivate |
| AC-ADMIN-2 | test_admin_requires_valid_key |
| AC-HEALTH-1 | test_readyz_reflects_database_reachability |
| AC-AUDIT-1 | test_tampering_is_detected |
| AC-SAFETY-1 | test_unsafe_targets_are_rejected |
| AC-PROXY-1 | test_analytics_count_real_clients_behind_proxy, test_rate_limits_use_the_real_client_behind_a_trusted_proxy |
| AC-PROXY-2 | test_spoofed_header_from_untrusted_peer_is_ignored |
| AC-HEADERS-1 | test_security_headers_on_every_response, test_security_headers_on_redirects |
| AC-CORS-1 | test_cors_allows_only_configured_origins |
