"""The redirect itself. Must be included last: GET /{code} matches any single path segment."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .context import AppContext
from .routes_links import ERRORS


def build(ctx: AppContext) -> APIRouter:
    router = APIRouter(tags=["redirect"])

    @router.get("/{code}", responses=ERRORS)
    def redirect(code: str, request: Request) -> RedirectResponse:
        """307 keeps the method; no-store means every click reaches us, so analytics stay accurate."""
        ctx.enforce(ctx.redirect_limiter, request, "redirect")
        target = ctx.service.resolve(code, referrer=request.headers.get("referer"),
                                     user_agent=request.headers.get("user-agent"), client_ip=ctx.client_ip(request))
        return RedirectResponse(target, status_code=307, headers={"Cache-Control": "no-store"})

    return router
