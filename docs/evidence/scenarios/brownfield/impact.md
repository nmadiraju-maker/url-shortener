# Impact analysis

**Risk:** high  |  **Schema change:** True

**Concepts searched:** CreateLinkRequest, Link, LinkExpired, LinkResponse, click_count, record_click, redirect, resolve, shorten, stats, summarise, to_response, validate_ttl

| Category | Items |
|---|---|
| Seed modules (direct) | urlshort.analytics, urlshort.errors, urlshort.models, urlshort.service, urlshort.storage, urlshort.validation, urlshort.web.context, urlshort.web.routes_links, urlshort.web.routes_redirect |
| Impacted (reverse import closure) | urlshort.analytics, urlshort.api, urlshort.audit, urlshort.config, urlshort.errors, urlshort.main, urlshort.models, urlshort.service, urlshort.storage, urlshort.validation, urlshort.web.context, urlshort.web.middleware, urlshort.web.routes_links, urlshort.web.routes_ops, urlshort.web.routes_redirect |
| API routes | POST /api/v1/links, GET /api/v1/links/{code}/stats, GET /{code} |
| Tables | clicks, links |
| Tests to update | tests/service/conftest.py, tests/service/test_analytics.py, tests/service/test_api.py, tests/service/test_audit.py, tests/service/test_config.py, tests/service/test_errors.py, tests/service/test_health.py, tests/service/test_main.py, tests/service/test_production.py, tests/service/test_service.py, tests/service/test_storage.py, tests/service/test_validation.py |
| Approved change scope | CHANGELOG.md, RELEASE_NOTES.md, VERSION, docs/*, docs/**, tests/*, tests/**, urlshort/analytics.py, urlshort/errors.py, urlshort/models.py, urlshort/service.py, urlshort/storage.py, urlshort/validation.py, urlshort/web/context.py, urlshort/web/routes_links.py, urlshort/web/routes_redirect.py |

## Dependency subgraph (impacted modules)
```mermaid
flowchart RL
    urlshort_analytics[urlshort.analytics] --> urlshort_storage[urlshort.storage]
    urlshort_api[urlshort.api] --> urlshort_config[urlshort.config]
    urlshort_api[urlshort.api] --> urlshort_service[urlshort.service]
    urlshort_api[urlshort.api] --> urlshort_storage[urlshort.storage]
    urlshort_api[urlshort.api] --> urlshort_web_context[urlshort.web.context]
    urlshort_api[urlshort.api] --> urlshort_web_middleware[urlshort.web.middleware]
    urlshort_audit[urlshort.audit] --> urlshort_storage[urlshort.storage]
    urlshort_config[urlshort.config] --> urlshort_validation[urlshort.validation]
    urlshort_main[urlshort.main] --> urlshort_api[urlshort.api]
    urlshort_main[urlshort.main] --> urlshort_config[urlshort.config]
    urlshort_service[urlshort.service] --> urlshort_audit[urlshort.audit]
    urlshort_service[urlshort.service] --> urlshort_config[urlshort.config]
    urlshort_service[urlshort.service] --> urlshort_errors[urlshort.errors]
    urlshort_service[urlshort.service] --> urlshort_storage[urlshort.storage]
    urlshort_service[urlshort.service] --> urlshort_validation[urlshort.validation]
    urlshort_storage[urlshort.storage] --> urlshort_errors[urlshort.errors]
    urlshort_validation[urlshort.validation] --> urlshort_errors[urlshort.errors]
    urlshort_web_context[urlshort.web.context] --> urlshort_config[urlshort.config]
    urlshort_web_context[urlshort.web.context] --> urlshort_errors[urlshort.errors]
    urlshort_web_context[urlshort.web.context] --> urlshort_models[urlshort.models]
    urlshort_web_context[urlshort.web.context] --> urlshort_service[urlshort.service]
    urlshort_web_context[urlshort.web.context] --> urlshort_storage[urlshort.storage]
    urlshort_web_middleware[urlshort.web.middleware] --> urlshort_config[urlshort.config]
    urlshort_web_middleware[urlshort.web.middleware] --> urlshort_errors[urlshort.errors]
    urlshort_web_routes_links[urlshort.web.routes_links] --> urlshort_errors[urlshort.errors]
    urlshort_web_routes_links[urlshort.web.routes_links] --> urlshort_models[urlshort.models]
    urlshort_web_routes_links[urlshort.web.routes_links] --> urlshort_web_context[urlshort.web.context]
    urlshort_web_routes_ops[urlshort.web.routes_ops] --> urlshort_web_context[urlshort.web.context]
    urlshort_web_routes_redirect[urlshort.web.routes_redirect] --> urlshort_web_context[urlshort.web.context]
    urlshort_web_routes_redirect[urlshort.web.routes_redirect] --> urlshort_web_routes_links[urlshort.web.routes_links]
```
