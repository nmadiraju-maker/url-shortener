"""Link management and analytics endpoints under /api/v1.

No `from __future__ import annotations` here: FastAPI must see the `Caller` dependency alias, which is defined
inside build(), as a real object rather than a string it would resolve in module scope.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response

from ..auth import Principal
from ..errors import NotFound, Unauthorized
from ..models import CreateLinkRequest, CreateLinkResponse, ErrorResponse, LinkResponse, StatsResponse
from .context import AppContext

ERRORS: dict[int | str, dict[str, Any]] = {s: {"model": ErrorResponse} for s in (400, 401, 404, 409, 410, 429, 503)}
OWNER_MAX = 64


def build(ctx: AppContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/links", responses=ERRORS)
    service = ctx.service

    def caller(x_api_key: str | None = Header(default=None),
               authorization: str | None = Header(default=None)) -> Principal | None:
        return ctx.principal(x_api_key, authorization)

    Caller = Annotated[Principal | None, Depends(caller)]

    def limit_creates(request: Request, response: Response) -> None:
        # Runs before the body is validated, so invalid requests count too (no free probing).
        response.headers.update(ctx.enforce(ctx.create_limiter, request, "create"))

    @router.post("", status_code=201, response_model=CreateLinkResponse, tags=["links"],
                 dependencies=[Depends(limit_creates)])
    def create_link(body: CreateLinkRequest, response: Response, who: Caller,
                    x_owner: str | None = Header(default=None)) -> CreateLinkResponse:
        """Create a short link. Returns 201 when created, 200 when an identical permanent link is reused.

        With an owner key the link belongs to that key's owner (X-Owner is ignored). Without a key, X-Owner
        is used, unless the service requires API keys for creation.
        """
        if who is None and ctx.settings.require_api_key:
            raise Unauthorized("an API key is required to create links")
        claimed = (x_owner or "anonymous").strip()[:OWNER_MAX] or "anonymous"
        owner = who.owner if who is not None and who.owner else claimed
        result = service.shorten(body.url, owner=owner, alias=body.custom_alias, ttl_seconds=body.ttl_seconds,
                                 max_clicks=body.max_clicks)
        if not result.created:
            response.status_code = 200
        response.headers["Cache-Control"] = "no-store"   # the body may contain a secret token
        return CreateLinkResponse(**ctx.to_response(result.link).model_dump(), stats_token=result.stats_token)

    @router.get("/{code}", response_model=LinkResponse, tags=["links"])
    def get_link(code: str) -> LinkResponse:
        return ctx.to_response(service.get(code))

    _register_owner_routes(router, ctx, Caller)
    return router


def _register_owner_routes(router: APIRouter, ctx: AppContext, caller_type: Any) -> None:
    """Stats and takedown: who may see or change a link depends on its owner."""
    service = ctx.service

    @router.get("/{code}/stats", response_model=StatsResponse, tags=["analytics"])
    def link_stats(code: str, who: caller_type, x_stats_token: str | None = Header(default=None)) -> dict[str, Any]:
        """Click analytics: the link's X-Stats-Token, its owner's API key, or an admin key.

        Without valid credentials the answer is always 401, even for codes that do not exist, so
        the endpoint cannot be used to discover which codes are in use.
        """
        if who is not None and who.is_admin:
            return service.stats(code)                       # admins may learn that a code does not exist
        link = service.repo.get_link(code)
        owns = who is not None and link is not None and who.may_manage(link.owner)
        if not (owns or service.can_view_stats(code, x_stats_token)):
            raise Unauthorized("valid X-Stats-Token or the owner's API key required")
        return service.stats(code)

    @router.delete("/{code}", status_code=204, tags=["links"])
    def delete_link(code: str, who: caller_type) -> Response:
        """Deactivate (soft-delete) a link: admins any link, owners their own. Recorded with the caller's name."""
        if who is None:
            raise Unauthorized("an API key is required")
        link = service.repo.get_link(code)
        if link is None or not who.may_manage(link.owner):
            raise NotFound(f"link '{code}' not found")       # never reveal that someone else's link exists
        service.deactivate(code, actor=who.actor)
        return Response(status_code=204)
