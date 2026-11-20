"""Unit tests for the PitchAPI HTTP client. No network: httpx.MockTransport + a fake clock."""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from scouting.adapters.pitchapi.client import (
    BACKOFF_BASE_S,
    MAX_RETRY_AFTER_S,
    PitchApiClient,
    build_path,
    parse_retry_after,
)
from scouting.adapters.pitchapi.errors import (
    PitchApiAuthError,
    PitchApiProtocolError,
    PitchApiRequestError,
    PitchApiRetryExhaustedError,
)

FAKE_KEY = "pk_test_not_a_real_key"
FIXED_NOW = datetime(2026, 11, 19, 18, 0, tzinfo=UTC)
PATH = "/v1/matches/m_abc123/players"

Handler = Callable[[httpx.Request], httpx.Response]
Scripted = httpx.Response | Exception


class FakeClock:
    """monotonic() + sleep() that advance together, recording every sleep."""

    def __init__(self) -> None:
        self.t = 1_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def scripted(*steps: Scripted) -> tuple[Handler, list[httpx.Request]]:
    """A transport handler that replays ``steps`` in order and records requests."""
    seen: list[httpx.Request] = []
    remaining: Iterator[Scripted] = iter(steps)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        step = next(remaining)
        if isinstance(step, Exception):
            raise step
        return step

    return handler, seen


def make_client(
    handler: Handler, clock: FakeClock, *, min_interval_s: float = 0.0, max_retries: int = 5
) -> PitchApiClient:
    return PitchApiClient(
        FAKE_KEY,
        base_url="https://api.test",
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        now=lambda: FIXED_NOW,
        rng=random.Random(0),
        min_interval_s=min_interval_s,
        max_retries=max_retries,
    )


def error_body(code: str, message: str = "msg") -> dict[str, object]:
    return {"error": {"code": code, "message": message}}


# --- success and expected 404s ---------------------------------------------------------


def test_get_success_returns_raw_data_with_nulls_preserved() -> None:
    payload = {"passes": None, "crosses": 0, "nested": {"xg": None}}
    handler, _ = scripted(httpx.Response(200, json={"data": payload}))
    with make_client(handler, FakeClock()) as client:
        result = client.get(PATH)
    assert result.ok
    assert result.status == 200
    assert result.data == payload  # null stays None, never coerced to 0
    assert result.fetched_at == FIXED_NOW


def test_get_sends_api_key_header_and_query_params() -> None:
    handler, seen = scripted(httpx.Response(200, json={"data": []}))
    with make_client(handler, FakeClock()) as client:
        client.get("/v1/leagues/l_abc123/matches", {"season": "2025/2026", "status": "played"})
    (request,) = seen
    assert request.headers["X-API-KEY"] == FAKE_KEY
    assert request.url.params["season"] == "2025/2026"
    assert request.url.params["status"] == "played"


@pytest.mark.parametrize("code", ["ANALYTICS_UNAVAILABLE", "RESOURCE_NOT_FOUND"])
def test_get_404_with_error_envelope_returns_negative_result_without_retry(code: str) -> None:
    handler, seen = scripted(httpx.Response(404, json=error_body(code, "not rated")))
    clock = FakeClock()
    with make_client(handler, clock) as client:
        result = client.get(PATH)
    assert not result.ok
    assert result.error_code == code
    assert result.error_message == "not rated"
    assert result.data is None
    assert len(seen) == 1
    assert clock.sleeps == []


# --- retries ----------------------------------------------------------------------------


def test_get_429_with_retry_after_seconds_waits_exactly_then_succeeds() -> None:
    handler, seen = scripted(
        httpx.Response(429, headers={"Retry-After": "7"}, json=error_body("RATE_LIMIT_EXCEEDED")),
        httpx.Response(200, json={"data": {"ok": True}}),
    )
    clock = FakeClock()
    with make_client(handler, clock) as client:
        result = client.get(PATH)
    assert result.ok
    assert len(seen) == 2
    assert clock.sleeps == [7.0]


def test_get_429_with_retry_after_http_date_waits_until_that_time() -> None:
    retry_at = format_datetime(FIXED_NOW + timedelta(seconds=12), usegmt=True)
    handler, _ = scripted(
        httpx.Response(429, headers={"Retry-After": retry_at}),
        httpx.Response(200, json={"data": {}}),
    )
    clock = FakeClock()
    with make_client(handler, clock) as client:
        client.get(PATH)
    assert clock.sleeps == [12.0]


def test_get_retry_after_above_cap_raises_without_retrying() -> None:
    too_long = str(int(MAX_RETRY_AFTER_S) + 1)
    handler, seen = scripted(
        httpx.Response(
            429, headers={"Retry-After": too_long}, json=error_body("RATE_LIMIT_EXCEEDED")
        )
    )
    with (
        make_client(handler, FakeClock()) as client,
        pytest.raises(PitchApiRetryExhaustedError) as exc,
    ):
        client.get(PATH)
    assert exc.value.status == 429
    assert exc.value.code == "RATE_LIMIT_EXCEEDED"
    assert len(seen) == 1


def test_get_5xx_then_success_backs_off_within_jitter_bounds() -> None:
    handler, seen = scripted(
        httpx.Response(500, json=error_body("INTERNAL_SERVER_ERROR")),
        httpx.Response(503, text="<html>gateway</html>"),  # non-JSON bodies happen on 5xx
        httpx.Response(200, json={"data": {}}),
    )
    clock = FakeClock()
    with make_client(handler, clock) as client:
        assert client.get(PATH).ok
    assert len(seen) == 3
    assert len(clock.sleeps) == 2
    for attempt, slept in enumerate(clock.sleeps):
        assert 0.0 <= slept <= BACKOFF_BASE_S * 2**attempt


def test_get_transport_error_retries() -> None:
    handler, seen = scripted(
        httpx.ConnectError("connection refused"),
        httpx.Response(200, json={"data": {}}),
    )
    with make_client(handler, FakeClock()) as client:
        assert client.get(PATH).ok
    assert len(seen) == 2


def test_get_retries_exhausted_raises_after_max_attempts() -> None:
    handler, seen = scripted(*(httpx.Response(503) for _ in range(3)))
    clock = FakeClock()
    with (
        make_client(handler, clock, max_retries=2) as client,
        pytest.raises(PitchApiRetryExhaustedError) as exc,
    ):
        client.get(PATH)
    assert exc.value.status == 503
    assert len(seen) == 3
    assert len(clock.sleeps) == 2  # no pointless sleep after the final attempt


# --- fatal errors -----------------------------------------------------------------------


def test_get_401_raises_auth_error_without_retry() -> None:
    handler, seen = scripted(httpx.Response(401, json=error_body("UNAUTHORIZED")))
    with make_client(handler, FakeClock()) as client, pytest.raises(PitchApiAuthError) as exc:
        client.get(PATH)
    assert exc.value.code == "UNAUTHORIZED"
    assert len(seen) == 1
    assert FAKE_KEY not in str(exc.value)


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "INVALID_PARAMETER"), (403, "PLAN_UPGRADE_REQUIRED"), (400, None)],
)
def test_get_other_client_error_raises_request_error_without_retry(
    status: int, code: str | None
) -> None:
    response = (
        httpx.Response(status, json=error_body(code)) if code else httpx.Response(status, text="?")
    )
    handler, seen = scripted(response)
    with make_client(handler, FakeClock()) as client, pytest.raises(PitchApiRequestError) as exc:
        client.get(PATH)
    assert exc.value.status == status
    assert exc.value.code == code
    assert len(seen) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[1, 2, 3]),
        httpx.Response(200, json={"data": {}, "error": {"code": "X"}}),
        httpx.Response(200, json={"something": "else"}),
        httpx.Response(200, json=error_body("ANALYTICS_UNAVAILABLE")),
        httpx.Response(404, json={"data": {}}),
        httpx.Response(404, json={"error": {"message": "no code"}}),
        httpx.Response(404, text="Not Found"),
    ],
    ids=[
        "2xx-not-json",
        "2xx-not-object",
        "both-keys",
        "neither-key",
        "2xx-error-envelope",
        "404-data-envelope",
        "404-error-without-code",
        "404-not-json",
    ],
)
def test_get_malformed_envelope_raises_protocol_error(response: httpx.Response) -> None:
    handler, _ = scripted(response)
    with make_client(handler, FakeClock()) as client, pytest.raises(PitchApiProtocolError):
        client.get(PATH)


# --- throttling, validation, hygiene ----------------------------------------------------


def test_get_consecutive_requests_are_spaced_by_min_interval() -> None:
    handler, _ = scripted(*(httpx.Response(200, json={"data": {}}) for _ in range(3)))
    clock = FakeClock()
    with make_client(handler, clock, min_interval_s=1.0) as client:
        for _ in range(3):
            client.get(PATH)
    assert clock.sleeps == [1.0, 1.0]  # first request goes immediately


def test_get_path_outside_v1_raises_value_error() -> None:
    handler, seen = scripted()
    with make_client(handler, FakeClock()) as client, pytest.raises(ValueError):
        client.get("/matches/m_abc123")
    assert seen == []


def test_repr_does_not_leak_api_key() -> None:
    handler, _ = scripted()
    with make_client(handler, FakeClock()) as client:
        assert FAKE_KEY not in repr(client)


@pytest.mark.parametrize(
    ("kwargs", "api_key"),
    [
        ({}, ""),
        ({}, " pk_test_padded "),
        ({"base_url": "http://api.pitchapi.dev"}, FAKE_KEY),
        ({"min_interval_s": -1.0}, FAKE_KEY),
        ({"max_retries": -1}, FAKE_KEY),
    ],
)
def test_init_invalid_arguments_raise_value_error(kwargs: dict[str, object], api_key: str) -> None:
    with pytest.raises(ValueError):
        PitchApiClient(api_key, **kwargs)  # type: ignore[arg-type]


# --- helpers ----------------------------------------------------------------------------


def test_build_path_percent_encodes_every_segment() -> None:
    assert build_path("matches", "m_abc123", "players") == "/v1/matches/m_abc123/players"
    assert build_path("players", "p_x/../../admin") == "/v1/players/p_x%2F..%2F..%2Fadmin"


@pytest.mark.parametrize("segments", [(), ("matches", ""), ("",)])
def test_build_path_empty_input_raises_value_error(segments: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        build_path(*segments)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("5", 5.0),
        (" 30 ", 30.0),
        ("0", 0.0),
        ("abc", None),
        ("", None),
        ("2.5", None),  # RFC 9110 delta-seconds are integers only
        ("²", None),  # Unicode digit, not ASCII
        (format_datetime(FIXED_NOW + timedelta(seconds=90), usegmt=True), 90.0),
        (format_datetime(FIXED_NOW - timedelta(seconds=90), usegmt=True), 0.0),  # already past
    ],
)
def test_parse_retry_after_table(header: str | None, expected: float | None) -> None:
    assert parse_retry_after(header, FIXED_NOW) == expected
