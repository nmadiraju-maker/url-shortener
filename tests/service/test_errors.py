import pytest

from urlshort.errors import (
    AliasConflict,
    CodeSpaceExhausted,
    DomainError,
    InvalidInput,
    LinkExpired,
    NotFound,
    RateLimited,
    Unauthorized,
)


@pytest.mark.parametrize("cls,code,status", [
    (DomainError, "internal_error", 500),
    (InvalidInput, "invalid_input", 400),
    (Unauthorized, "unauthorized", 401),
    (NotFound, "not_found", 404),
    (AliasConflict, "alias_conflict", 409),
    (LinkExpired, "link_expired", 410),
    (CodeSpaceExhausted, "code_generation_failed", 503),
])
def test_each_error_has_stable_code_and_status(cls: type[DomainError], code: str, status: int) -> None:
    err = cls("boom")
    assert (err.code, err.status, err.message, str(err)) == (code, status, "boom", "boom")
    assert isinstance(err, DomainError)


def test_rate_limited_carries_retry_after() -> None:
    err = RateLimited("slow down", retry_after=30)
    assert (err.code, err.status, err.retry_after, err.message) == ("rate_limited", 429, 30, "slow down")


def test_rate_limited_carries_headers() -> None:
    err = RateLimited("slow down", retry_after=7, headers={"Retry-After": "7", "RateLimit-Limit": "10"})
    assert err.headers == {"Retry-After": "7", "RateLimit-Limit": "10"}
    assert RateLimited("x", retry_after=3).headers == {"Retry-After": "3"}
