"""API contracts (request/response schemas). These generate the OpenAPI document at /docs.

Requests reject unknown fields, so a typo such as "ttl" instead of "ttl_seconds" is a 400, not a
link silently created without an expiry. Range rules live in validation.py (one source of truth for
every caller); fixed bounds are repeated below only so OpenAPI shows them. Configurable limits
(URL length, maximum TTL) are deliberately not repeated here, so the config stays authoritative.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., examples=["https://example.com/some/long/path?x=1"])  # length limit is configurable
    custom_alias: str | None = Field(None, min_length=3, max_length=32, examples=["spring-sale"])
    ttl_seconds: int | None = Field(None, ge=1, examples=[86400])


class LinkResponse(BaseModel):
    code: str
    short_url: str
    target_url: str
    created_at: datetime
    expires_at: datetime | None
    is_active: bool
    click_count: int


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
