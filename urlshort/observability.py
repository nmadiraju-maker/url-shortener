"""Prometheus metrics and OpenTelemetry tracing.

Metrics (per-app registry, so app instances and tests never share state):
  urlshort_http_requests_total{method,route,status}       route = the route TEMPLATE ("/{code}"), never the raw
  urlshort_http_request_duration_seconds{method,route}    path: random short codes must not explode cardinality
  urlshort_redirects_total{outcome}                       redirected / not_found / gone / rate_limited / unavailable
  urlshort_links_created_total
  urlshort_rate_limited_total{limit}                      create / redirect
  urlshort_event_outbox_backlog                           events mode: clicks waiting to be published (at scrape)

Tracing: one span per request, continuing an incoming W3C `traceparent`, exported over OTLP/HTTP when
`otel_endpoint` is set. The active trace ID is added to every JSON log line, so logs and traces join up.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
REDIRECT_OUTCOMES = {307: "redirected", 404: "not_found", 410: "gone", 429: "rate_limited", 503: "unavailable"}


class _Backlog(Collector):
    def __init__(self, read: Callable[[], int]) -> None:
        self._read = read

    def collect(self) -> Iterator[GaugeMetricFamily]:
        yield GaugeMetricFamily("urlshort_event_outbox_backlog", "Click events waiting to be published",
                                value=self._read())


class Metrics:
    def __init__(self, outbox_backlog: Callable[[], int] | None = None) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter("urlshort_http_requests_total", "HTTP requests", ["method", "route", "status"],
                                registry=self.registry)
        self.latency = Histogram("urlshort_http_request_duration_seconds", "HTTP request latency",
                                 ["method", "route"], buckets=LATENCY_BUCKETS, registry=self.registry)
        self.redirects = Counter("urlshort_redirects_total", "Redirect outcomes", ["outcome"], registry=self.registry)
        self.links_created = Counter("urlshort_links_created_total", "Links created", registry=self.registry)
        self.rate_limited = Counter("urlshort_rate_limited_total", "Requests refused by rate limits", ["limit"],
                                    registry=self.registry)
        if outbox_backlog is not None:
            self.registry.register(_Backlog(outbox_backlog))

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        self.requests.labels(method, route, str(status)).inc()
        self.latency.labels(method, route).observe(seconds)
        if route == "/{code}" and method == "GET":
            self.redirects.labels(REDIRECT_OUTCOMES.get(status, "other")).inc()
        elif route == "/api/v1/links" and method == "POST" and status == 201:
            self.links_created.inc()


def tracer_provider(service_name: str, endpoint: str = "", exporter: SpanExporter | None = None) -> TracerProvider:
    """A tracer provider exporting to `exporter` (tests) or OTLP/HTTP at `endpoint`; no export if neither."""
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if exporter is None and endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter(endpoint=endpoint)
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
