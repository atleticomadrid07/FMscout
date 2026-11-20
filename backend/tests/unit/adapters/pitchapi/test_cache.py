"""Unit tests for the cache layer: freshness policies, negative caching, offline mode.

Uses a fake client and a controllable clock; no network, no real waiting.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from scouting.adapters.pitchapi.cache import (
    FINISHED_MATCH,
    MATCH_LIST_COMPLETED,
    MATCH_LIST_CURRENT,
    CachedFetcher,
    CacheMissError,
    FreshnessPolicy,
    cache_key,
)
from scouting.adapters.pitchapi.client import ApiResponse, PitchApiClient
from scouting.adapters.pitchapi.errors import PitchApiRetryExhaustedError
from scouting.adapters.pitchapi.raw_store import RawStore

T0 = datetime(2026, 11, 19, 18, 0, tzinfo=UTC)
PATH = "/v1/matches/m_abc123/advanced/players"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeClient:
    """Returns scripted responses (or raises scripted exceptions) and records calls."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.script: list[ApiResponse | Exception] = []

    def ok(self, data: object) -> FakeClient:
        self.script.append(self._response(200, data=data))
        return self

    def not_found(self, code: str) -> FakeClient:
        self.script.append(self._response(404, code=code))
        return self

    def fail(self, exc: Exception) -> FakeClient:
        self.script.append(exc)
        return self

    def _response(self, status: int, data: object = None, code: str | None = None) -> ApiResponse:
        # path/params are filled in at call time; ApiResponse is frozen, so rebuild then.
        return ApiResponse(
            path="",
            params={},
            status=status,
            data=data,
            error_code=code,
            error_message="msg" if code else None,
            fetched_at=T0,
        )

    def get(self, path: str, params: Mapping[str, str] | None = None) -> ApiResponse:
        query = dict(params or {})
        self.calls.append((path, query))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return ApiResponse(
            path=path,
            params=query,
            status=step.status,
            data=step.data,
            error_code=step.error_code,
            error_message=step.error_message,
            fetched_at=self.clock(),
        )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RawStore]:
    s = RawStore(tmp_path / "raw.db")
    yield s
    s.close()


@pytest.fixture
def client(clock: Clock) -> FakeClient:
    return FakeClient(clock)


def make(store: RawStore, client: FakeClient | None, clock: Clock) -> CachedFetcher:
    return CachedFetcher(store, client, now=clock)


# --- cache_key ----------------------------------------------------------------------------


def test_cache_key_without_params_is_method_and_path() -> None:
    assert cache_key("/v1/leagues") == "GET /v1/leagues"
    assert cache_key("/v1/leagues", {}) == "GET /v1/leagues"


def test_cache_key_is_independent_of_param_order() -> None:
    a = cache_key("/v1/x", {"season": "2025/2026", "status": "played"})
    b = cache_key("/v1/x", {"status": "played", "season": "2025/2026"})
    assert a == b == "GET /v1/x?season=2025%2F2026&status=played"


# --- miss, hit, policies --------------------------------------------------------------------


def test_get_miss_fetches_stores_and_returns(
    store: RawStore, client: FakeClient, clock: Clock
) -> None:
    client.ok({"players": []})
    fetcher = make(store, client, clock)
    result = fetcher.get(PATH, policy=FINISHED_MATCH)
    assert result.ok and not result.from_cache
    assert result.data == {"players": []}
    assert store.count() == 1
    assert fetcher.stats.network_fetches == 1


def test_get_second_call_is_served_from_cache(
    store: RawStore, client: FakeClient, clock: Clock
) -> None:
    client.ok({"players": []})
    fetcher = make(store, client, clock)
    fetcher.get(PATH, policy=FINISHED_MATCH)
    clock.advance(timedelta(days=3650))  # finished-match data never goes stale
    result = fetcher.get(PATH, policy=FINISHED_MATCH)
    assert result.from_cache
    assert len(client.calls) == 1
    assert fetcher.stats.cache_hits == 1


def test_get_params_are_part_of_the_key(store: RawStore, client: FakeClient, clock: Clock) -> None:
    client.ok({"matches": [1]}).ok({"matches": [2]})
    fetcher = make(store, client, clock)
    path = "/v1/leagues/l_4WFCIZ/matches"
    a = fetcher.get(path, {"season": "2024/2025"}, policy=MATCH_LIST_COMPLETED)
    b = fetcher.get(path, {"season": "2025/2026"}, policy=MATCH_LIST_COMPLETED)
    assert a.data != b.data
    assert len(client.calls) == 2


def test_get_ttl_zero_always_refetches(store: RawStore, client: FakeClient, clock: Clock) -> None:
    client.ok({"v": 1}).ok({"v": 2})
    fetcher = make(store, client, clock)
    path = "/v1/leagues/l_4WFCIZ/matches"
    fetcher.get(path, policy=MATCH_LIST_CURRENT)
    result = fetcher.get(path, policy=MATCH_LIST_CURRENT)
    assert not result.from_cache
    assert result.data == {"v": 2}
    assert store.count() == 1  # upserted, not duplicated


def test_get_finite_ttl_expires(store: RawStore, client: FakeClient, clock: Clock) -> None:
    policy = FreshnessPolicy("test", ttl=timedelta(days=7), negative_ttl=None)
    client.ok({"v": 1}).ok({"v": 2})
    fetcher = make(store, client, clock)
    fetcher.get("/v1/leagues", policy=policy)

    clock.advance(timedelta(days=7) - timedelta(seconds=1))
    assert fetcher.get("/v1/leagues", policy=policy).from_cache

    clock.advance(timedelta(seconds=1))  # exactly at expiry: stale
    result = fetcher.get("/v1/leagues", policy=policy)
    assert not result.from_cache
    assert result.data == {"v": 2}


def test_get_changed_policy_applies_to_stored_responses(
    store: RawStore, client: FakeClient, clock: Clock
) -> None:
    """A season that was current when fetched but is complete now stops being refetched."""
    client.ok({"v": 1})
    fetcher = make(store, client, clock)
    path = "/v1/leagues/l_4WFCIZ/matches"
    fetcher.get(path, policy=MATCH_LIST_CURRENT)
    assert fetcher.get(path, policy=MATCH_LIST_COMPLETED).from_cache
    assert len(client.calls) == 1


# --- negative cache ---------------------------------------------------------------------------


def test_get_404_is_cached_until_negative_ttl(
    store: RawStore, client: FakeClient, clock: Clock
) -> None:
    client.not_found("ANALYTICS_UNAVAILABLE").ok({"players": ["late"]})
    fetcher = make(store, client, clock)

    first = fetcher.get(PATH, policy=FINISHED_MATCH)
    assert not first.ok and first.error_code == "ANALYTICS_UNAVAILABLE"

    clock.advance(timedelta(days=6))
    again = fetcher.get(PATH, policy=FINISHED_MATCH)
    assert again.from_cache and again.error_code == "ANALYTICS_UNAVAILABLE"

    clock.advance(timedelta(days=1))  # 7 days: re-check, and the analytics have appeared
    late = fetcher.get(PATH, policy=FINISHED_MATCH)
    assert late.ok and not late.from_cache
    assert late.data == {"players": ["late"]}
    assert len(client.calls) == 2


def test_get_client_exception_propagates_and_stores_nothing(
    store: RawStore, client: FakeClient, clock: Clock
) -> None:
    client.fail(PitchApiRetryExhaustedError("gave up", status=503))
    fetcher = make(store, client, clock)
    with pytest.raises(PitchApiRetryExhaustedError):
        fetcher.get(PATH, policy=FINISHED_MATCH)
    assert store.count() == 0


def test_get_exception_on_refetch_keeps_the_old_response(
    store: RawStore, client: FakeClient, clock: Clock
) -> None:
    policy = FreshnessPolicy("test", ttl=timedelta(days=1), negative_ttl=None)
    client.ok({"v": 1}).fail(PitchApiRetryExhaustedError("gave up", status=503))
    fetcher = make(store, client, clock)
    fetcher.get("/v1/leagues", policy=policy)
    clock.advance(timedelta(days=2))
    with pytest.raises(PitchApiRetryExhaustedError):
        fetcher.get("/v1/leagues", policy=policy)
    stored = store.get(cache_key("/v1/leagues"))
    assert stored is not None and stored.data == {"v": 1}


# --- offline mode -------------------------------------------------------------------------------


def test_offline_serves_stale_responses(store: RawStore, client: FakeClient, clock: Clock) -> None:
    client.ok({"v": 1})
    path = "/v1/leagues/l_4WFCIZ/matches"
    make(store, client, clock).get(path, policy=MATCH_LIST_CURRENT)

    offline = make(store, None, clock)
    result = offline.get(path, policy=MATCH_LIST_CURRENT)  # stale under this policy
    assert offline.offline
    assert result.from_cache and result.data == {"v": 1}
    assert offline.stats.stale_served_offline == 1


def test_offline_miss_raises(store: RawStore, clock: Clock) -> None:
    with pytest.raises(CacheMissError):
        make(store, None, clock).get(PATH, policy=FINISHED_MATCH)


# --- policy validation ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ttl", "negative_ttl"),
    [(timedelta(seconds=-1), None), (None, timedelta(seconds=-1))],
)
def test_policy_negative_durations_raise(
    ttl: timedelta | None, negative_ttl: timedelta | None
) -> None:
    with pytest.raises(ValueError):
        FreshnessPolicy("bad", ttl=ttl, negative_ttl=negative_ttl)


# --- with the real client --------------------------------------------------------------------


def test_with_real_client_second_get_makes_no_http_request(store: RawStore, clock: Clock) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": {"leagues": []}})

    with PitchApiClient(
        "pk_test_not_a_real_key",
        base_url="https://api.test",
        transport=httpx.MockTransport(handler),
        min_interval_s=0.0,
        now=clock,
    ) as client:
        fetcher = CachedFetcher(store, client, now=clock)
        first = fetcher.get("/v1/leagues", policy=FINISHED_MATCH)
        second = fetcher.get("/v1/leagues", policy=FINISHED_MATCH)

    assert first.data == second.data == {"leagues": []}
    assert not first.from_cache and second.from_cache
    assert len(requests) == 1
