"""raw.db: every PitchAPI response we've received, stored compressed (D-004, SCHEMA §3).

This uses the stdlib `sqlite3` module rather than SQLAlchemy: it's one table, only the
sync CLI touches it, it's never hosted, and it isn't managed by Alembic. The schema
version lives in `PRAGMA user_version`; opening a file with a different version fails
loudly instead of guessing.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = 1

# A normal rowid table: WITHOUT ROWID is a poor fit for rows carrying large BLOBs.
_CREATE_TABLE: Final = """
CREATE TABLE raw_response (
    cache_key     TEXT    PRIMARY KEY,
    path          TEXT    NOT NULL,
    params_json   TEXT    NOT NULL,
    status        INTEGER NOT NULL,
    error_code    TEXT,
    error_message TEXT,
    body_gz       BLOB,
    fetched_at    TEXT    NOT NULL
)
"""

_UPSERT: Final = """
INSERT INTO raw_response
    (cache_key, path, params_json, status, error_code, error_message, body_gz, fetched_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(cache_key) DO UPDATE SET
    path = excluded.path,
    params_json = excluded.params_json,
    status = excluded.status,
    error_code = excluded.error_code,
    error_message = excluded.error_message,
    body_gz = excluded.body_gz,
    fetched_at = excluded.fetched_at
"""

_SELECT: Final = """
SELECT cache_key, path, params_json, status, error_code, error_message, body_gz, fetched_at
FROM raw_response WHERE cache_key = ?
"""

JsonValue = Any  # raw JSON; typed parsing happens in the normalization layer


class RawStoreError(RuntimeError):
    """raw.db is unusable as-is (e.g. written by a different schema version)."""


@dataclass(frozen=True, slots=True)
class RawRecord:
    """One stored response: a 2xx `data` payload, or a 404 error (negative cache)."""

    cache_key: str
    path: str
    params: Mapping[str, str]
    status: int
    error_code: str | None
    error_message: str | None
    data: JsonValue  # None for error records
    fetched_at: datetime

    @property
    def ok(self) -> bool:
        return self.error_code is None


def _encode_body(data: JsonValue) -> bytes:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # mtime=0 keeps the bytes deterministic: same payload, same blob.
    return gzip.compress(text.encode("utf-8"), mtime=0)


def _decode_body(blob: bytes) -> JsonValue:
    return json.loads(gzip.decompress(blob).decode("utf-8"))


class RawStore:
    """Read and upsert raw responses. Every `put` is committed immediately, so a crawl
    killed mid-run keeps everything it already fetched."""

    def __init__(self, db_path: Path | str) -> None:
        if isinstance(db_path, Path):
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        try:
            self._prepare_schema()
        except Exception:
            self._conn.close()
            raise

    def _prepare_schema(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        (version,) = self._conn.execute("PRAGMA user_version").fetchone()
        if version == 0:
            with self._conn:
                self._conn.execute(_CREATE_TABLE)
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            raise RawStoreError(
                f"raw.db has schema version {version}, this code expects {SCHEMA_VERSION}. "
                "Delete it to re-crawl, or migrate it."
            )

    def __enter__(self) -> RawStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def get(self, cache_key: str) -> RawRecord | None:
        row = self._conn.execute(_SELECT, (cache_key,)).fetchone()
        if row is None:
            return None
        key, path, params_json, status, error_code, error_message, body_gz, fetched_at = row
        return RawRecord(
            cache_key=key,
            path=path,
            params=json.loads(params_json),
            status=status,
            error_code=error_code,
            error_message=error_message,
            data=None if body_gz is None else _decode_body(body_gz),
            fetched_at=datetime.fromisoformat(fetched_at),
        )

    def put(self, record: RawRecord) -> None:
        if record.fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware (UTC)")
        params_json = json.dumps(dict(sorted(record.params.items())), ensure_ascii=False)
        body = _encode_body(record.data) if record.ok else None
        with self._conn:
            self._conn.execute(
                _UPSERT,
                (
                    record.cache_key,
                    record.path,
                    params_json,
                    record.status,
                    record.error_code,
                    record.error_message,
                    body,
                    record.fetched_at.isoformat(),
                ),
            )

    def count(self) -> int:
        (n,) = self._conn.execute("SELECT COUNT(*) FROM raw_response").fetchone()
        return int(n)
