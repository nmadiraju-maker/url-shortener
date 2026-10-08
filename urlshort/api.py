"""App factory: wires configuration, storage, service, rate limiters, middleware and routers.

Each call builds an isolated app (own settings, repository, clocks), which is what lets every test run
against a fresh instance. The HTTP behaviour itself lives in urlshort.web.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI
from opentelemetry.sdk.trace.export import SpanExporter

from . import __version__
from .config import DEFAULT_IP_SALT, Settings, ensure_safe_for_environment
from .logging_setup import configure_logging
from .observability import Metrics, tracer_provider
from .ratelimit import GcraLimiter, RateLimiter
from .service import ShortenerService, utcnow
from .storage import Repository, SqliteRepository
from .web import routes_links, routes_ops, routes_redirect
from .web.clientip import TrustedProxies
from .web.context import AppContext
from .web.middleware import install_error_handlers, install_middleware

log = logging.getLogger("urlshort.api")


def create_app(settings: Settings | None = None, repo: Repository | None = None, *,
               clock: Callable[[], datetime] = utcnow, monotonic: Callable[[], float] = time.monotonic,
               span_exporter: SpanExporter | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    ensure_safe_for_environment(settings)   # also covers Settings built in code, not only loaded from config
    configure_logging(settings.log_level, settings.log_redact_keys)
    _log_startup(settings)
    repository, limiters = _infrastructure(settings, repo)
    create_limiter, redirect_limiter = limiters or (
        GcraLimiter(settings.create_rate_per_minute, settings.create_burst, clock=monotonic,
                    max_keys=settings.rate_limit_max_keys),
        GcraLimiter(settings.redirect_rate_per_minute, settings.redirect_burst, clock=monotonic,
                    max_keys=settings.rate_limit_max_keys))
    metrics = Metrics(repository.outbox_backlog if settings.analytics_mode == "events" else None)
    provider = tracer_provider(settings.otel_service_name, settings.otel_endpoint, span_exporter)
    ctx = AppContext(
        settings=settings, repository=repository, service=ShortenerService(repository, settings, clock=clock),
        create_limiter=create_limiter, redirect_limiter=redirect_limiter,
        proxies=TrustedProxies(settings.trusted_proxies), metrics=metrics)
    docs = settings.expose_docs
    app = FastAPI(title="URL Shortener", version=__version__, docs_url="/docs" if docs else None,
                  redoc_url="/redoc" if docs else None, openapi_url="/openapi.json" if docs else None)
    app.state.service, app.state.metrics, app.state.tracer_provider = ctx.service, metrics, provider
    install_error_handlers(app)
    install_middleware(app, settings, metrics, provider.get_tracer("urlshort"))
    app.include_router(routes_ops.build(ctx))
    app.include_router(routes_links.build(ctx))
    app.include_router(routes_redirect.build(ctx))   # last: it matches any single path segment
    return app


def _infrastructure(settings: Settings,
                    repo: Repository | None) -> tuple[Repository, tuple[RateLimiter, RateLimiter] | None]:
    """Storage and (optionally) shared rate limiters. Postgres/Redis drivers load only when configured."""
    if settings.database_url or settings.redis_url:
        from .adapters.wiring import build
        return build(settings, repo)
    return repo or SqliteRepository(settings.db_path), None


def _log_startup(settings: Settings) -> None:
    log.info("service configured", extra={"settings": settings.summary()})
    if settings.ip_hash_salt == DEFAULT_IP_SALT:
        log.warning("URLSHORT_IP_SALT is the built-in default; visitor IDs are weakly protected until it is set")
    if not settings.admin_api_key:
        log.info("URLSHORT_ADMIN_API_KEY not set; admin endpoints are disabled")
    if settings.trusted_proxies:
        log.info("X-Forwarded-For is trusted only from configured proxies",
                 extra={"trusted_proxies": sorted(settings.trusted_proxies)})
