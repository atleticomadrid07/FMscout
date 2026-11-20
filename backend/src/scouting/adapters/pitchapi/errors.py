"""Exceptions raised by the PitchAPI adapter.

Expected "no data" outcomes (404 RESOURCE_NOT_FOUND, ANALYTICS_UNAVAILABLE) are
NOT exceptions: they come back as an ApiResponse with ``ok == False`` so the
cache layer can negative-cache them (D-004). Exceptions are reserved for
conditions a sync has to stop or skip on.
"""

from __future__ import annotations


class PitchApiError(Exception):
    """Base class. ``status``/``code`` are None when no usable response was received."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class PitchApiAuthError(PitchApiError):
    """401: key missing, malformed or revoked. Abort the sync."""


class PitchApiRequestError(PitchApiError):
    """Non-retryable 4xx other than 401/404 (e.g. 400 INVALID_PARAMETER): a bug on our side."""


class PitchApiProtocolError(PitchApiError):
    """The response did not match the documented envelope. Fail loudly rather than guess."""


class PitchApiRetryExhaustedError(PitchApiError):
    """Gave up on a retryable failure (429, 5xx, network), or Retry-After exceeded our cap."""
