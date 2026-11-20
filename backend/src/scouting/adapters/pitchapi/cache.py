"""Cache layer in front of PitchApiClient (D-004).

`CachedFetcher.get()` returns a stored response when it's still fresh under the given
`FreshnessPolicy`, and otherwise fetches, stores and returns a new one. Freshness is
decided at read time from `fetched_at` + the policy, so changing a TTL applies to
responses that are already stored.

Only terminal outcomes are cached: 2xx responses and 404s (ANALYTICS_UNAVAILABLE,
RESOURCE_NOT_FOUND). Anything the client raises on (401, 400, retries exhausted)
propagates and is never stored.

Without a client the fetcher is offline: it serves what's stored, even if stale, and
raises CacheMissError for anything missing. That's how scouting.db gets rebuilt from
raw.db without touching the API.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from urllib.parse import urlencode

from scouting.adapters.pitchapi.client import ApiResponse
from scouting.adapters.pitchapi.raw_store import JsonValue, RawRecord, RawStore


class ApiGetter(Protocol):
    """What the fetcher needs from a client. PitchApiClient satisfies it; tests use fakes."""

    def get(self, path: str, params: Mapping[str, str] | None = None) -> ApiResponse: ...


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """How long a stored response stays valid.

    `ttl` applies to successful responses and `negative_ttl` to 404s. None means the
    response never goes stale; timedelta(0) means it's always refetched.
    """

    name: str
    ttl: timedelta | None
    negative_ttl: timedelta | None

    def __post_init__(self) -> None:
        for label, value in (("ttl", self.ttl), ("negative_ttl", self.negative_ttl)):
            if value is not None and value < timedelta(0):
                raise ValueError(f"{self.name}: {label} must not be negative")


# Endpoint classes from D-004. Adjust here; stored responses pick up the change.
LEAGUES: Final = FreshnessPolicy("leagues", ttl=timedelta(days=7), negative_ttl=timedelta(days=1))
MATCH_LIST_CURRENT: Final = FreshnessPolicy(
    "match_list_current", ttl=timedelta(0), negative_ttl=timedelta(0)
)
MATCH_LIST_COMPLETED: Final = FreshnessPolicy(
    "match_list_completed", ttl=None, negative_ttl=timedelta(days=1)
)
# Finished-match data never changes; a 404 such as ANALYTICS_UNAVAILABLE is re-checked
# weekly, which also answers the open "do analytics appear late?" question over time.
FINISHED_MATCH: Final = FreshnessPolicy("finished_match", ttl=None, negative_ttl=timedelta(days=7))
PROFILE: Final = FreshnessPolicy("profile", ttl=timedelta(days=90), negative_ttl=timedelta(days=30))


class CacheMissError(LookupError):
    """Offline mode, and the response was never fetched."""


def cache_key(path: str, params: Mapping[str, str] | None = None) -> str:
    """Deterministic key: method + path + query params sorted by name."""
    if not params:
        return f"GET {path}"
    return f"GET {path}?{urlencode(sorted(params.items()))}"


@dataclass(frozen=True, slots=True)
class FetchResult:
    record: RawRecord
    from_cache: bool

    @property
    def ok(self) -> bool:
        return self.record.ok

    @property
    def data(self) -> JsonValue:
        return self.record.data

    @property
    def error_code(self) -> str | None:
        return self.record.error_code


@dataclass(slots=True)
class FetchStats:
    """Counters for the end-of-sync summary."""

    cache_hits: int = 0
    network_fetches: int = 0
    stale_served_offline: int = 0


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CachedFetcher:
    def __init__(
        self,
        store: RawStore,
        client: ApiGetter | None,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._store = store
        self._client = client
        self._now = now
        self.stats = FetchStats()

    @property
    def offline(self) -> bool:
        return self._client is None

    def get(
        self,
        path: str,
        params: Mapping[str, str] | None = None,
        *,
        policy: FreshnessPolicy,
    ) -> FetchResult:
        query = dict(params) if params else {}
        key = cache_key(path, query)
        cached = self._store.get(key)

        if cached is not None and self._is_fresh(cached, policy):
            self.stats.cache_hits += 1
            return FetchResult(cached, from_cache=True)

        if self._client is None:
            if cached is not None:
                self.stats.stale_served_offline += 1
                return FetchResult(cached, from_cache=True)
            raise CacheMissError(f"offline and not cached: {key}")

        response = self._client.get(path, query)
        record = RawRecord(
            cache_key=key,
            path=response.path,
            params=dict(response.params),
            status=response.status,
            error_code=response.error_code,
            error_message=response.error_message,
            data=response.data,
            fetched_at=response.fetched_at,
        )
        self._store.put(record)
        self.stats.network_fetches += 1
        return FetchResult(record, from_cache=False)

    def _is_fresh(self, record: RawRecord, policy: FreshnessPolicy) -> bool:
        ttl = policy.ttl if record.ok else policy.negative_ttl
        if ttl is None:
            return True
        return self._now() < record.fetched_at + ttl
