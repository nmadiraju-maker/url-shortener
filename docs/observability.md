# Observability

## Metrics (Prometheus)

`GET /metrics` (excluded from the API docs). In production `URLSHORT_METRICS_TOKEN` is required and Prometheus
sends it as a bearer token (`deploy/observability/prometheus.yml`); `[observability] metrics_enabled = false`
removes the endpoint.

| Metric | Labels | Meaning |
|---|---|---|
| `urlshort_http_requests_total` | method, route, status | Requests by route **template** (`/{code}`), never the raw path, so random codes cannot explode cardinality |
| `urlshort_http_request_duration_seconds` | method, route | Latency histogram (1 ms to 2.5 s) |
| `urlshort_redirects_total` | outcome | redirected / not_found / gone / rate_limited / unavailable |
| `urlshort_links_created_total` | | Links created |
| `urlshort_rate_limited_total` | limit | Refusals by the create or redirect limiter |
| `urlshort_event_outbox_backlog` | | Events mode: clicks waiting to be published |

`deploy/observability/alerts.yml` (error rate, redirect p99 vs NFR-1, rate-limit spikes, growing event backlog,
capped links failing closed) and `grafana-dashboard.json` are checked by a test: every metric they reference
must be one the service exports.

## Tracing (OpenTelemetry)

Set `URLSHORT_OTEL_ENDPOINT` (OTLP/HTTP, e.g. `http://jaeger:4318/v1/traces`) to export one span per request,
named by route template, with method, route, status and request ID attributes; 5xx responses mark the span as
an error. An incoming W3C `traceparent` is continued, so the service joins the caller's trace.

**Logs join traces:** every JSON log line written during a request carries `trace_id`. Find a slow trace in
Jaeger, then filter the logs by its ID (or the reverse).

## Operational behaviour during outages

Measured by the chaos drills (`docs/evidence/chaos.md`): repeated failures are logged at most once per 30
seconds with a count of suppressed repeats, so an outage cannot flood the log pipeline; Postgres failures trip
a circuit breaker so requests fail (or fail open) at once instead of waiting.
