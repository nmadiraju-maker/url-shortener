"""Cross-cutting HTTP behaviour: request IDs and request logs, security headers, error envelope, CORS."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

from ..config import Settings
from ..errors import DomainError, RateLimited
from ..logging_setup import request_id_var, sampled

log = logging.getLogger("urlshort.api")
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})
# Locked down for an API that only ever returns JSON or redirects. The interactive docs page loads
# scripts and styles from a CDN, so it is the one place these two headers are not applied.
API_ONLY_HEADERS = {"Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
                    "X-Frame-Options": "DENY"}


def error_response(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    """The single error format used for every failure, including unexpected ones."""
    body = {"error": {"code": code, "message": message, "request_id": request_id_var.get()}}
    return JSONResponse(status_code=status, content=body, headers=headers)


def security_headers(settings: Settings, path: str) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",   # destinations see our origin, never full URLs
        "X-Robots-Tag": "noindex, nofollow",                    # short links and API responses are not content
    }
    if path not in DOCS_PATHS:
        headers.update(API_ONLY_HEADERS)
    if settings.base_url.startswith("https://") and settings.hsts_max_age > 0:
        headers["Strict-Transport-Security"] = f"max-age={settings.hsts_max_age}; includeSubDomains"
    return headers


def install_middleware(app: FastAPI, settings: Settings) -> None:
    if settings.cors_allow_origins:   # off unless origins are configured
        app.add_middleware(
            CORSMiddleware, allow_origins=sorted(settings.cors_allow_origins), allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "X-Request-ID", "X-Owner", "X-Stats-Token", "X-API-Key"],
            expose_headers=["X-Request-ID", "Retry-After", "RateLimit-Limit", "RateLimit-Remaining",
                            "RateLimit-Reset"],
            max_age=600)

    # Added last, so it is the outermost layer: even CORS preflights and crashes get an ID and headers.
    @app.middleware("http")
    async def correlate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:  # last line of defence: unexpected bugs still get the JSON format + request ID
                log.exception("unhandled error", extra={"method": request.method, "path": request.url.path})
                response = error_response(500, "internal_error", "an unexpected error occurred")
            response.headers["x-request-id"] = rid
            for name, value in security_headers(settings, request.url.path).items():
                response.headers.setdefault(name, value)
            # Errors are always logged; successful requests can be sampled (decided by request ID).
            if response.status_code >= 400 or sampled(rid, settings.log_request_sample_rate):
                log.info("request", extra={"method": request.method, "path": request.url.path,
                                           "status": response.status_code,
                                           "duration_ms": round((time.perf_counter() - started) * 1000, 2)})
            return response
        finally:
            request_id_var.reset(token)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        headers = exc.headers if isinstance(exc, RateLimited) else None
        return error_response(exc.status, exc.code, exc.message, headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"] if p != "body")
        return error_response(400, "invalid_input", f"{where}: {first['msg']}" if where else first["msg"])
