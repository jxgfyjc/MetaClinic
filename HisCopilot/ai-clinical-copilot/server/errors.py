"""Uniform API error model: every failure is `{"error": {"code", "message", "details", "request_id"}}`."""
from __future__ import annotations

from typing import Any


class ApiError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None,
                 details: Any = None, headers: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status
        self.details = details
        self.headers = headers or {}


class NotFound(ApiError):
    status, code = 404, "not_found"


class Forbidden(ApiError):
    status, code = 403, "forbidden"


class Unauthorized(ApiError):
    status, code = 401, "unauthenticated"


class Conflict(ApiError):
    status, code = 409, "conflict"


class Unprocessable(ApiError):
    status, code = 422, "invalid"


class PreconditionFailed(ApiError):
    status, code = 412, "precondition_failed"


class StaleVersion(ApiError):
    """Optimistic-lock failure: the client edited an outdated copy."""
    status, code = 409, "stale_version"


class TooMany(ApiError):
    status, code = 429, "rate_limited"


class Unavailable(ApiError):
    status, code = 503, "unavailable"
