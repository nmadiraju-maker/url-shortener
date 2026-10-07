"""Domain errors. Each carries a stable machine-readable code and the HTTP status it maps to.

The service layer raises these; the HTTP adapter turns them into one consistent JSON error format.
"""

from __future__ import annotations


class DomainError(Exception):
    code = "internal_error"
    status = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidInput(DomainError):
    code, status = "invalid_input", 400


class Unauthorized(DomainError):
    code, status = "unauthorized", 401


class NotFound(DomainError):
    code, status = "not_found", 404


class AliasConflict(DomainError):
    code, status = "alias_conflict", 409


class LinkExpired(DomainError):
    code, status = "link_expired", 410


class RateLimited(DomainError):
    code, status = "rate_limited", 429

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class CodeSpaceExhausted(DomainError):
    code, status = "code_generation_failed", 503
