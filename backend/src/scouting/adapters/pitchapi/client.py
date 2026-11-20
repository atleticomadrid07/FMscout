"""Low-level HTTP client for PitchAPI (https://pitchapi.dev).

Why our own client rather than the official SDK (D-009):
- The SDK's dataclasses default missing counts to 0, which breaks null != 0.
  This client returns the raw ``data`` payload untouched.
- Raw JSON is only reachable through the SDK's private ``_get``, which can
  change in any 0.1.x release.

Scope: one GET at a time, with throttling, retries and envelope validation.
Caching (D-004) and typed parsing belong to other layers.
"""

from __future__ import annotations

import email.utils
import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, NamedTuple
from urllib.parse import quote

import httpx

from scouting.adapters.pitchapi.errors import (
    PitchApiAuthError,
    PitchApiProtocolError,
    PitchApiRequestError,
    PitchApiRetryExhaustedError,
)

logger = logging.getLogger(__name__)

BASE_URL: Final = "https://api.pitchapi.dev"
API_PREFIX: Final = "/v1/"
API_KEY_HEADER: Final = "X-API-KEY"

DEFAULT_TIMEOUT_S: Final = 30.0
# The burst guard's threshold is unpublished, so we pick a conservative pace.
DEFAULT_MIN_INTERVAL_S: Final = 1.0
DEFAULT_MAX_RETRIES: Final = 5
BACKOFF_BASE_S: Final = 1.0
BACKOFF_CAP_S: Final = 30.0
# A Retry-After longer than this means "stop and resume the crawl later",
# not "block the process for minutes".
MAX_RETRY_AFTER_S: Final = 300.0

HTTP_UNAUTHORIZED: Final = 401
HTTP_NOT_FOUND: Final = 404
RETRYABLE_STATUSES: Final = frozenset({429, 500, 502, 503, 504})

JsonValue = Any  # raw JSON; typed parsing happens in the normalization layer


@dataclass(frozen=True, slots=True)
class ApiResponse:
    """A terminal API outcome: a 2xx ``data`` payload, or a 404 error envelope."""

    path: str
    params: Mapping[str, str]
    status: int
    data: JsonValue
    error_code: str | None
    error_message: str | None
    fetched_at: datetime

    @property
    def ok(self) -> bool:
        return self.error_code is None


class _Envelope(NamedTuple):
    data: JsonValue
    error_code: str | None
    error_message: str | None


def build_path(*segments: str) -> str:
    """Build ``/v1/a/b/c`` with every segment percent-encoded.

    IDs are opaque and never parsed, but they still end up in a URL path, so a
    malformed ID must not be able to change which endpoint we hit.
    """
    if not segments:
        raise ValueError("at least one path segment is required")
    if any(not segment for segment in segments):
        raise ValueError(f"empty path segment in {segments!r}")
    return API_PREFIX + "/".join(quote(segment, safe="") for segment in segments)


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date).

    Returns None when the header is absent or unparseable, so the caller falls
    back to exponential backoff.
    """
    if value is None:
        return None
    value = value.strip()
    # isascii() guards against Unicode digits like "²", which isdigit() accepts.
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_envelope(path: str, response: httpx.Response) -> _Envelope:
    status = response.status_code
    try:
        body = response.json()
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError both subclass it
        raise PitchApiProtocolError(f"GET {path}: response is not JSON", status=status) from exc
    if not isinstance(body, dict):
        raise PitchApiProtocolError(f"GET {path}: top-level JSON is not an object", status=status)
    has_data, has_error = "data" in body, "error" in body
    if has_data == has_error:
        raise PitchApiProtocolError(
            f"GET {path}: expected exactly one of 'data' or 'error'", status=status
        )
    if has_data:
        return _Envelope(body["data"], None, None)
    error = body["error"]
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        raise PitchApiProtocolError(
            f"GET {path}: 'error' must be an object with a string 'code'", status=status
        )
    message = error.get("message")
    return _Envelope(None, error["code"], message if isinstance(message, str) else None)


def _error_details(path: str, response: httpx.Response) -> tuple[str | None, str | None]:
    """Best-effort (code, message) for responses we're about to raise on anyway."""
    try:
        envelope = _parse_envelope(path, response)
    except PitchApiProtocolError:
        return None, None
    return envelope.error_code, envelope.error_message


class PitchApiClient:
    """Synchronous PitchAPI client. Use it as a context manager, or call ``close()``.

    ``transport``, ``sleep``, ``monotonic``, ``now`` and ``rng`` exist so tests
    can run without the network or real waiting.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _utc_now,
        rng: random.Random | None = None,
    ) -> None:
        if not api_key or api_key.strip() != api_key:
            raise ValueError("api_key must be non-empty with no surrounding whitespace")
        if not base_url.startswith("https://"):
            # PitchAPI refuses plain HTTP, and the key must never travel unencrypted.
            raise ValueError("base_url must use https://")
        if min_interval_s < 0:
            raise ValueError("min_interval_s must be >= 0")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")

        self._http = httpx.Client(
            base_url=base_url,
            headers={API_KEY_HEADER: api_key, "Accept": "application/json"},
            timeout=timeout_s,
            transport=transport,
        )
        self._min_interval_s = min_interval_s
        self._max_retries = max_retries
        self._sleep = sleep
        self._monotonic = monotonic
        self._now = now
        self._rng = rng if rng is not None else random.Random()
        self._last_request_at: float | None = None

    def __repr__(self) -> str:
        # Deliberately excludes headers: the API key must never reach logs or tracebacks.
        return f"PitchApiClient(base_url={str(self._http.base_url)!r})"

    def __enter__(self) -> PitchApiClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get(self, path: str, params: Mapping[str, str] | None = None) -> ApiResponse:
        """GET ``path``. Returns 2xx and 404 outcomes; raises on everything else.

        Retries 429/5xx/network errors, honouring Retry-After when present and
        otherwise backing off exponentially with full jitter.
        """
        if not path.startswith(API_PREFIX):
            raise ValueError(f"path must start with {API_PREFIX!r}; build it with build_path()")
        query = dict(params) if params else {}
        attempts = self._max_retries + 1
        last_failure = ""
        last_status: int | None = None

        for attempt in range(attempts):
            self._throttle()
            try:
                response = self._http.get(path, params=query)
            except httpx.TransportError as exc:
                last_failure, last_status = f"{type(exc).__name__}: {exc}", None
                delay = self._backoff(attempt)
            else:
                if response.status_code not in RETRYABLE_STATUSES:
                    return self._to_api_response(path, query, response)
                last_failure, last_status = f"HTTP {response.status_code}", response.status_code
                delay = self._retry_delay(path, response, attempt)

            if attempt + 1 < attempts:
                logger.warning(
                    "GET %s failed (%s); retry %d/%d in %.1fs",
                    path,
                    last_failure,
                    attempt + 1,
                    self._max_retries,
                    delay,
                )
                self._sleep(delay)

        raise PitchApiRetryExhaustedError(
            f"GET {path} failed after {attempts} attempts; last: {last_failure}",
            status=last_status,
        )

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            wait = self._min_interval_s - (self._monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._monotonic()

    def _backoff(self, attempt: int) -> float:
        # Full jitter: spreads retries out instead of having them all fire in lockstep.
        return self._rng.uniform(0.0, min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2**attempt))

    def _retry_delay(self, path: str, response: httpx.Response, attempt: int) -> float:
        retry_after = parse_retry_after(response.headers.get("Retry-After"), self._now())
        if retry_after is None:
            return self._backoff(attempt)
        if retry_after > MAX_RETRY_AFTER_S:
            code, _ = _error_details(path, response)
            raise PitchApiRetryExhaustedError(
                f"GET {path}: Retry-After {retry_after:.0f}s exceeds the "
                f"{MAX_RETRY_AFTER_S:.0f}s cap; resume the crawl later",
                status=response.status_code,
                code=code,
            )
        return retry_after

    def _to_api_response(
        self, path: str, query: Mapping[str, str], response: httpx.Response
    ) -> ApiResponse:
        status = response.status_code
        if status == HTTP_UNAUTHORIZED:
            code, _ = _error_details(path, response)
            raise PitchApiAuthError(
                f"GET {path}: unauthorized; check PITCHAPI_API_KEY", status=status, code=code
            )
        if not response.is_success and status != HTTP_NOT_FOUND:
            code, message = _error_details(path, response)
            detail = " ".join(part for part in (code, message) if part)
            raise PitchApiRequestError(
                f"GET {path}: HTTP {status} {detail}".rstrip(), status=status, code=code
            )

        envelope = _parse_envelope(path, response)
        if response.is_success and envelope.error_code is not None:
            raise PitchApiProtocolError(
                f"GET {path}: error envelope on HTTP {status}", status=status
            )
        if status == HTTP_NOT_FOUND and envelope.error_code is None:
            raise PitchApiProtocolError(
                f"GET {path}: HTTP 404 without an error envelope", status=status
            )

        return ApiResponse(
            path=path,
            params=query,
            status=status,
            data=envelope.data,
            error_code=envelope.error_code,
            error_message=envelope.error_message,
            fetched_at=self._now(),
        )
