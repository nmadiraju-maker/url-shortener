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

from . import __version__
from .config import DEFAULT_IP_SALT, Settings
from .logging_setup import configure_logging
from .ratelimit import GcraLimiter
from .service import ShortenerService, utcnow
from .storage import Repository, SqliteRepository
from .web import routes_links, routes_ops, routes_redirect
from .web.clientip import TrustedProxies
from .web.context import AppContext
from .web.middleware import install_error_handlers, install_middleware

log = logging.getLogger("urlshort.api")


def create_app(settings: Settings | None = None, repo: Repository | None = None, *,
               clock: Callable[[], datetime] = utcnow, monotonic: Callable[[], float] = time.monotonic) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level, settings.log_redact_keys)
    _log_startup(settings)
    repository: Repository = repo or SqliteRepository(settings.db_path)
    ctx = AppContext(
        settings=settings, repository=repository, service=ShortenerService(repository, settings, clock=clock),
        create_limiter=GcraLimiter(settings.create_rate_per_minute, settings.create_burst, clock=monotonic,
                                   max_keys=settings.rate_limit_max_keys),
        redirect_limiter=GcraLimiter(settings.redirect_rate_per_minute, settings.redirect_burst, clock=monotonic,
                                     max_keys=settings.rate_limit_max_keys),
        proxies=TrustedProxies(settings.trusted_proxies))
    docs = settings.expose_docs
    app = FastAPI(title="URL Shortener", version=__version__, docs_url="/docs" if docs else None,
                  redoc_url="/redoc" if docs else None, openapi_url="/openapi.json" if docs else None)
    app.state.service = ctx.service
    install_error_handlers(app)
    install_middleware(app, settings)
    app.include_router(routes_ops.build(ctx))
    app.include_router(routes_links.build(ctx))
    app.include_router(routes_redirect.build(ctx))   # last: it matches any single path segment
    return app


def _log_startup(settings: Settings) -> None:
    log.info("service configured", extra={"settings": settings.summary()})
    if settings.ip_hash_salt == DEFAULT_IP_SALT:
        log.warning("URLSHORT_IP_SALT is the built-in default; visitor IDs are weakly protected until it is set")
    if not settings.admin_api_key:
        log.info("URLSHORT_ADMIN_API_KEY not set; admin endpoints are disabled")
    if settings.trusted_proxies:
        log.info("X-Forwarded-For is trusted only from configured proxies",
                 extra={"trusted_proxies": sorted(settings.trusted_proxies)})
