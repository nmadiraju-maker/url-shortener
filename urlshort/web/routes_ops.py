"""Health endpoints for orchestrators and load balancers."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .. import __version__
from ..errors import Unauthorized
from .context import AppContext


def build(ctx: AppContext) -> APIRouter:
    router = APIRouter(tags=["ops"])

    @router.get("/livez")
    @router.get("/healthz")   # kept as an alias: older probes and docs use it
    def livez() -> dict[str, str]:
        """Liveness: the process is up and serving requests. Restart the container if this fails."""
        return {"status": "ok", "version": __version__}

    if ctx.settings.metrics_enabled:
        @router.get("/metrics", include_in_schema=False)
        def metrics(authorization: str | None = Header(default=None)) -> Response:
            """Prometheus metrics. With a metrics token configured, `Authorization: Bearer <token>` is required."""
            token = ctx.settings.metrics_token
            if token and not hmac.compare_digest(authorization or "", f"Bearer {token}"):
                raise Unauthorized("valid metrics token required")
            return Response(generate_latest(ctx.metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @router.get("/readyz")
    def readyz() -> JSONResponse:
        """Readiness: dependencies (the database) are reachable. Stop routing traffic if this fails."""
        ok = ctx.repository.ping()
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "degraded"})

    return router
