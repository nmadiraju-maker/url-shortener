"""Link management and analytics endpoints under /api/v1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response

from ..errors import Unauthorized
from ..models import CreateLinkRequest, CreateLinkResponse, ErrorResponse, LinkResponse, StatsResponse
from .context import AppContext

ERRORS: dict[int | str, dict[str, Any]] = {s: {"model": ErrorResponse} for s in (400, 401, 404, 409, 410, 429, 503)}
OWNER_MAX = 64


def build(ctx: AppContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/links", responses=ERRORS)
    service = ctx.service

    def require_admin(x_api_key: str | None = Header(default=None)) -> str:
        if not ctx.is_admin(x_api_key):
            raise Unauthorized("valid X-API-Key required")
        return "admin"

    def limit_creates(request: Request, response: Response) -> None:
        # Runs before the body is validated, so invalid requests count too (no free probing).
        response.headers.update(ctx.enforce(ctx.create_limiter, request, "create"))

    @router.post("", status_code=201, response_model=CreateLinkResponse, tags=["links"],
                 dependencies=[Depends(limit_creates)])
    def create_link(body: CreateLinkRequest, response: Response,
                    x_owner: str | None = Header(default=None)) -> CreateLinkResponse:
        """Create a short link. Returns 201 when created, 200 when an identical permanent link is reused."""
        owner = (x_owner or "anonymous").strip()[:OWNER_MAX] or "anonymous"
        result = service.shorten(body.url, owner=owner, alias=body.custom_alias, ttl_seconds=body.ttl_seconds)
        if not result.created:
            response.status_code = 200
        response.headers["Cache-Control"] = "no-store"   # the body may contain a secret token
        return CreateLinkResponse(**ctx.to_response(result.link).model_dump(), stats_token=result.stats_token)

    @router.get("/{code}", response_model=LinkResponse, tags=["links"])
    def get_link(code: str) -> LinkResponse:
        return ctx.to_response(service.get(code))

    @router.get("/{code}/stats", response_model=StatsResponse, tags=["analytics"])
    def link_stats(code: str, x_stats_token: str | None = Header(default=None),
                   x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
        """Click analytics. Requires the link's X-Stats-Token, or the admin X-API-Key.

        Without valid credentials the answer is always 401, even for codes that do not exist, so
        the endpoint cannot be used to discover which codes are in use.
        """
        if not (ctx.is_admin(x_api_key) or service.can_view_stats(code, x_stats_token)):
            raise Unauthorized("valid X-Stats-Token required")
        return service.stats(code)

    @router.delete("/{code}", status_code=204, tags=["links"])
    def delete_link(code: str, actor: str = Depends(require_admin)) -> Response:
        """Deactivate (soft-delete) a link. Admin only."""
        service.deactivate(code, actor=actor)
        return Response(status_code=204)

    return router
