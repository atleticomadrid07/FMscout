"""Unit tests for raw.db storage. Uses temporary files; no network."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scouting.adapters.pitchapi.raw_store import SCHEMA_VERSION, RawRecord, RawStore, RawStoreError

T0 = datetime(2026, 11, 19, 18, 0, tzinfo=UTC)


def ok_record(key: str = "GET /v1/x", data: object = None, at: datetime = T0) -> RawRecord:
    return RawRecord(
        cache_key=key,
        path="/v1/x",
        params={"season": "2025/2026", "status": "played"},
        status=200,
        error_code=None,
        error_message=None,
        data={"a": 1} if data is None else data,
        fetched_at=at,
    )


def error_record(key: str = "GET /v1/m") -> RawRecord:
    return RawRecord(
        cache_key=key,
        path="/v1/m",
        params={},
        status=404,
        error_code="ANALYTICS_UNAVAILABLE",
        error_message="not rated",
        data=None,
        fetched_at=T0,
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RawStore]:
    s = RawStore(tmp_path / "raw.db")
    yield s
    s.close()


def test_get_unknown_key_returns_none(store: RawStore) -> None:
    assert store.get("GET /v1/nothing") is None


def test_put_then_get_round_trips_payload_with_nulls_and_unicode(store: RawStore) -> None:
    payload = {"name": "Miloš Kerkez", "xg": None, "shots": 0, "nested": [{"v": 1.5}]}
    store.put(ok_record(data=payload))
    got = store.get("GET /v1/x")
    assert got is not None
    assert got.ok
    assert got.data == payload  # None stays None, 0 stays 0
    assert got.params == {"season": "2025/2026", "status": "played"}
    assert got.fetched_at == T0


def test_put_error_record_round_trips_without_body(store: RawStore) -> None:
    store.put(error_record())
    got = store.get("GET /v1/m")
    assert got is not None
    assert not got.ok
    assert got.error_code == "ANALYTICS_UNAVAILABLE"
    assert got.status == 404
    assert got.data is None


def test_put_same_key_twice_upserts(store: RawStore) -> None:
    store.put(ok_record(data={"v": 1}))
    later = datetime(2026, 11, 20, tzinfo=UTC)
    store.put(ok_record(data={"v": 2}, at=later))
    got = store.get("GET /v1/x")
    assert got is not None
    assert got.data == {"v": 2}
    assert got.fetched_at == later
    assert store.count() == 1


def test_put_naive_datetime_raises(store: RawStore) -> None:
    with pytest.raises(ValueError):
        store.put(ok_record(at=datetime(2026, 11, 19, 18, 0)))


def test_body_is_stored_compressed(tmp_path: Path) -> None:
    path = tmp_path / "raw.db"
    big = {"rows": [{"key": "accurate_passes", "value": i} for i in range(500)]}
    with RawStore(path) as s:
        s.put(ok_record(data=big))
    (blob,) = sqlite3.connect(path).execute("SELECT body_gz FROM raw_response").fetchone()
    assert blob[:2] == b"\x1f\x8b"  # gzip magic number
    assert len(blob) < 2000


def test_data_survives_reopening_the_file(tmp_path: Path) -> None:
    path = tmp_path / "raw.db"
    with RawStore(path) as s:
        s.put(ok_record())
    with RawStore(path) as s:
        assert s.get("GET /v1/x") is not None


def test_new_file_gets_current_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "raw.db"
    RawStore(path).close()
    (version,) = sqlite3.connect(path).execute("PRAGMA user_version").fetchone()
    assert version == SCHEMA_VERSION


def test_open_file_with_other_schema_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "raw.db"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(RawStoreError):
        RawStore(path)


def test_missing_parent_directory_is_created(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "raw.db"
    RawStore(path).close()
    assert path.exists()
