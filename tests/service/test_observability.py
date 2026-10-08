"""Prometheus metrics, OpenTelemetry tracing and log/trace correlation."""
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from urlshort.api import create_app
from urlshort.config import Settings, load_settings
from urlshort.logging_setup import configure_logging, shutdown_logging
from urlshort.observability import current_trace_id, tracer_provider
from urlshort.storage import SqliteRepository

from .helpers import FakeClock

PARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def scrape(client: TestClient, **headers: str) -> str:
    resp = client.get("/metrics", headers=headers)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/plain")
    return resp.text


def test_metrics_use_route_templates_not_raw_paths(client: TestClient) -> None:
    code = client.post("/api/v1/links", json={"url": "https://example.com/m"}).json()["code"]
    client.get(f"/{code}", follow_redirects=False)
    for i in range(20):                                         # 20 random codes: still ONE series
        client.get(f"/nope{i:03d}", follow_redirects=False)
    text = scrape(client)
    assert 'urlshort_http_requests_total{method="GET",route="/{code}",status="404"} 20.0' in text
    assert 'urlshort_redirects_total{outcome="redirected"} 1.0' in text
    assert 'urlshort_redirects_total{outcome="not_found"} 20.0' in text
    assert "urlshort_links_created_total 1.0" in text and "nope0" not in text
    assert 'urlshort_http_request_duration_seconds_bucket{le="0.005",method="POST",route="/api/v1/links"}' in text


def test_rate_limit_refusals_are_counted(repo: SqliteRepository, clock: FakeClock) -> None:
    client = TestClient(create_app(Settings(create_burst=1), repo, clock=clock))
    for i in range(3):
        client.post("/api/v1/links", json={"url": f"https://example.com/{i}"})
    assert 'urlshort_rate_limited_total{limit="create"} 2.0' in scrape(client)


def test_metrics_token_and_disabling(repo: SqliteRepository, clock: FakeClock) -> None:
    guarded = TestClient(create_app(Settings(metrics_token="scrape-me"), repo, clock=clock))
    assert guarded.get("/metrics").status_code == 401
    assert guarded.get("/metrics", headers={"authorization": "Bearer wrong"}).status_code == 401
    assert "urlshort_http_requests_total" in scrape(guarded, authorization="Bearer scrape-me")
    off = TestClient(create_app(Settings(metrics_enabled=False), repo, clock=clock))
    assert off.get("/metrics", follow_redirects=False).status_code == 404       # just an unknown short code


def test_event_backlog_gauge(repo: SqliteRepository, clock: FakeClock) -> None:
    client = TestClient(create_app(Settings(analytics_mode="events"), repo, clock=clock))
    code = client.post("/api/v1/links", json={"url": "https://example.com/e"}).json()["code"]
    client.get(f"/{code}", follow_redirects=False)
    assert "urlshort_event_outbox_backlog 1.0" in scrape(client)


def traced(repo: SqliteRepository, clock: FakeClock) -> tuple[TestClient, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    app = create_app(Settings(), repo, clock=clock, span_exporter=exporter)

    def boom() -> None:
        raise RuntimeError("bug")
    app.add_api_route("/x/boom", boom)
    return TestClient(app), exporter


def finished(client: TestClient, exporter: InMemorySpanExporter) -> list:  # type: ignore[type-arg]
    client.app.state.tracer_provider.force_flush()  # type: ignore[attr-defined]
    return list(exporter.get_finished_spans())


def test_spans_per_request_continue_incoming_traces(repo: SqliteRepository, clock: FakeClock) -> None:
    client, exporter = traced(repo, clock)
    client.get("/missing", headers={"traceparent": PARENT}, follow_redirects=False)
    client.get("/x/boom")
    spans = finished(client, exporter)
    redirect, failure = spans[0], spans[1]
    assert redirect.name == "GET /{code}" and redirect.attributes["http.route"] == "/{code}"
    assert redirect.attributes["http.response.status_code"] == 404
    assert format(redirect.context.trace_id, "032x") == PARENT.split("-")[1]          # same trace as the caller
    assert format(redirect.parent.span_id, "016x") == PARENT.split("-")[2]
    assert failure.status.status_code == StatusCode.ERROR and failure.name == "GET /x/boom"


def test_log_lines_carry_the_trace_id(repo: SqliteRepository, clock: FakeClock) -> None:
    client, exporter = traced(repo, clock)
    shutdown_logging()
    buf = io.StringIO()
    configure_logging("INFO", stream=buf)
    try:
        client.get("/missing", headers={"traceparent": PARENT}, follow_redirects=False)
    finally:
        shutdown_logging()                                       # flush the background queue
    (line,) = [json.loads(x) for x in buf.getvalue().splitlines() if '"msg": "request"' in x]
    assert line["trace_id"] == PARENT.split("-")[1]              # the log line joins the trace
    assert current_trace_id() is None                            # nothing active outside a request
    configure_logging()


def test_otlp_export_is_configured_from_the_endpoint() -> None:
    provider = tracer_provider("urlshort", "http://127.0.0.1:1/v1/traces")
    processors = provider._active_span_processor._span_processors
    assert type(processors[0]._batch_processor._exporter).__name__ == "OTLPSpanExporter"
    assert tracer_provider("urlshort")._active_span_processor._span_processors == ()   # no endpoint: no export
    provider.shutdown()


def test_observability_settings(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('[observability]\nmetrics_enabled = false\notel_endpoint = "http://otel:4318/v1/traces"\n'
                    'otel_service_name = "urlshort-eu"\n')
    s = load_settings(path, {"URLSHORT_METRICS_TOKEN": "t"})
    assert (s.metrics_enabled, s.otel_endpoint, s.otel_service_name) == (False, "http://otel:4318/v1/traces",
                                                                        "urlshort-eu")
    assert s.summary()["metrics_token"] == "set"


@pytest.mark.parametrize("path", ["/livez", "/readyz"])
def test_probes_are_measured_too(client: TestClient, path: str) -> None:
    client.get(path)
    assert f'route="{path}",status="200"' in scrape(client)


def test_dashboards_and_alerts_reference_only_real_metrics(client: TestClient) -> None:
    """A renamed metric must fail CI, not leave a dashboard silently showing 'No data'."""
    import re
    root = Path(__file__).resolve().parents[2] / "deploy" / "observability"
    referenced = set()
    for name in ("alerts.yml", "grafana-dashboard.json"):
        referenced |= set(re.findall(r"urlshort_[a-z_]+", (root / name).read_text()))
    events = TestClient(create_app(Settings(analytics_mode="events"), SqliteRepository()))
    events.get("/livez")
    # Declared families (always present), not observed samples: a labelled series appears only after its
    # first observation, but the family is exported from the start.
    families = re.findall(r"^# TYPE (urlshort_[a-z_]+) (\w+)", scrape(events), re.M)
    suffixes = {"counter": ("_total",), "histogram": ("_bucket", "_count", "_sum"), "gauge": ("",)}
    exported = {name.removesuffix("_total") + suffix for name, kind in families for suffix in suffixes[kind]}
    assert referenced and referenced <= exported, sorted(referenced - exported)
    json.loads((root / "grafana-dashboard.json").read_text())                  # valid JSON
