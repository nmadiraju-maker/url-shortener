"""HTTP adapter (FastAPI). Thin by design: transport concerns only, business rules live in the service.

Built through an app factory so each test (and each worker) gets an isolated app with its own
settings, repository and clock.
"""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse

from . import __version__
from .config import Settings
from .errors import DomainError, Unauthorized
from .logging_setup import configure_logging, request_id_var
from .models import CreateLinkRequest, CreateLinkResponse, ErrorResponse, LinkResponse, StatsResponse
from .service import ShortenerService, utcnow
from .storage import Link, Repository, SqliteRepository

log = logging.getLogger("urlshort.api")
ERRORS: dict[int | str, dict[str, Any]] = {s: {"model": ErrorResponse} for s in (400, 401, 404, 409, 410, 503)}
OWNER_MAX = 64


def error_response(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    """The single error format used for every failure, including unexpected ones."""
    body = {"error": {"code": code, "message": message, "request_id": request_id_var.get()}}
    return JSONResponse(status_code=status, content=body, headers=headers)


def create_app(settings: Settings | None = None, repo: Repository | None = None, *,
               clock: Callable[[], datetime] = utcnow) -> FastAPI:
    settings = settings or Settings.from_env()
    repository: Repository = repo or SqliteRepository(settings.db_path)
    configure_logging()
    service = ShortenerService(repository, settings, clock=clock)
    app = FastAPI(title="URL Shortener", version=__version__)
    app.state.service = service

    def to_response(link: Link) -> LinkResponse:
        return LinkResponse(code=link.code, short_url=f"{settings.base_url}/{link.code}",
                            target_url=link.target_url, created_at=link.created_at,
                            expires_at=link.expires_at, is_active=link.is_active, click_count=link.click_count)

    def is_admin(x_api_key: str | None) -> bool:
        # No configured key means admin access is disabled, never open.
        # compare_digest takes the same time however close a guess is (no timing side channel).
        return bool(settings.admin_api_key and x_api_key and hmac.compare_digest(x_api_key, settings.admin_api_key))

    def require_admin(x_api_key: str | None = Header(default=None)) -> str:
        if not is_admin(x_api_key):
            raise Unauthorized("valid X-API-Key required")
        return "admin"

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
            # Log while the request ID is still bound, so this line carries it.
            log.info("request", extra={"method": request.method, "path": request.url.path,
                                       "status": response.status_code,
                                       "duration_ms": round((time.perf_counter() - started) * 1000, 2)})
            return response
        finally:
            request_id_var.reset(token)

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"] if p != "body")
        return error_response(400, "invalid_input", f"{where}: {first['msg']}" if where else first["msg"])

    # ---------------------------------------------------------------- ops
    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Liveness: the process is up and serving requests."""
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", tags=["ops"])
    def readyz() -> JSONResponse:
        """Readiness: dependencies (the database) are reachable."""
        ok = repository.ping()
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "degraded"})

    # ---------------------------------------------------------------- links
    @app.post("/api/v1/links", status_code=201, response_model=CreateLinkResponse, responses=ERRORS,
              tags=["links"])
    def create_link(body: CreateLinkRequest, response: Response,
                    x_owner: str | None = Header(default=None)) -> CreateLinkResponse:
        """Create a short link. Returns 201 when created, 200 when an identical permanent link is reused."""
        owner = (x_owner or "anonymous").strip()[:OWNER_MAX] or "anonymous"
        result = service.shorten(body.url, owner=owner, alias=body.custom_alias, ttl_seconds=body.ttl_seconds)
        if not result.created:
            response.status_code = 200
        response.headers["Cache-Control"] = "no-store"   # the body may contain a secret token
        return CreateLinkResponse(**to_response(result.link).model_dump(), stats_token=result.stats_token)

    @app.get("/api/v1/links/{code}", response_model=LinkResponse, responses=ERRORS, tags=["links"])
    def get_link(code: str) -> LinkResponse:
        return to_response(service.get(code))

    @app.get("/api/v1/links/{code}/stats", response_model=StatsResponse, responses=ERRORS, tags=["analytics"])
    def link_stats(code: str, x_stats_token: str | None = Header(default=None),
                   x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        """Click analytics. Requires the link's X-Stats-Token, or the admin X-API-Key.

        Without valid credentials the answer is always 401, even for codes that do not exist, so
        the endpoint cannot be used to discover which codes are in use.
        """
        if not (is_admin(x_api_key) or service.can_view_stats(code, x_stats_token)):
            raise Unauthorized("valid X-Stats-Token required")
        return service.stats(code)

    @app.delete("/api/v1/links/{code}", status_code=204, responses=ERRORS, tags=["links"])
    def delete_link(code: str, actor: str = Depends(require_admin)) -> Response:
        """Deactivate (soft-delete) a link. Admin only."""
        service.deactivate(code, actor=actor)
        return Response(status_code=204)

    # Defined last: it matches any single path segment.
    @app.get("/{code}", responses=ERRORS, tags=["redirect"])
    def redirect(code: str, request: Request) -> RedirectResponse:
        """307 keeps the method; no-store means every click reaches us, so analytics stay accurate."""
        target = service.resolve(code, referrer=request.headers.get("referer"),
                                 user_agent=request.headers.get("user-agent"),
                                 client_ip=request.client.host if request.client else None)
        return RedirectResponse(target, status_code=307, headers={"Cache-Control": "no-store"})

    return app
