"""Health endpoints for orchestrators and load balancers."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import __version__
from .context import AppContext


def build(ctx: AppContext) -> APIRouter:
    router = APIRouter(tags=["ops"])

    @router.get("/livez")
    @router.get("/healthz")   # kept as an alias: older probes and docs use it
    def livez() -> dict[str, str]:
        """Liveness: the process is up and serving requests. Restart the container if this fails."""
        return {"status": "ok", "version": __version__}

    @router.get("/readyz")
    def readyz() -> JSONResponse:
        """Readiness: dependencies (the database) are reachable. Stop routing traffic if this fails."""
        ok = ctx.repository.ping()
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "degraded"})

    return router
