"""CLI tests: `scouting sync matches` end to end, offline, against temporary databases."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from typer.testing import CliRunner

from scouting.adapters.pitchapi.cache import cache_key
from scouting.adapters.pitchapi.raw_store import RawRecord, RawStore
from scouting.cli.main import app
from scouting.db.schema_version import alembic_config, head_revision

runner = CliRunner()
T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
MATCHES_PATH = "/v1/leagues/l_FIXT01/matches"
MATCHES_PARAMS = {"season": "2025/2026", "status": "played"}


def _seed_raw_db(path: Path, load_fixture: Any) -> None:
    """Put the two responses a sync needs into raw.db, as if fetched earlier."""
    with RawStore(path) as store:
        for api_path, params, name in (
            ("/v1/leagues", {}, "leagues.json"),
            (MATCHES_PATH, MATCHES_PARAMS, "match_list.json"),
        ):
            store.put(
                RawRecord(
                    cache_key=cache_key(api_path, params),
                    path=api_path,
                    params=params,
                    status=200,
                    error_code=None,
                    error_message=None,
                    data=load_fixture(name),
                    fetched_at=T0,
                )
            )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path | str]:
    """Point Settings at temporary databases; env vars win over any real .env file."""
    database_url = f"sqlite:///{tmp_path / 'scouting.db'}"
    raw_path = tmp_path / "raw.db"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RAW_DB_PATH", str(raw_path))
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("PITCHAPI_API_KEY", "")
    return {"database_url": database_url, "raw_path": raw_path}


def _sync(*extra: str) -> Any:
    args = ["sync", "matches", "--league", "l_FIXT01", "--season", "2025/2026", *extra]
    return runner.invoke(app, args)


needs_migrations = pytest.mark.skipif(
    head_revision() is None, reason="no migrations yet: generate the first one"
)


@needs_migrations
def test_sync_matches_offline_prints_summary(env: dict[str, Any], load_fixture: Any) -> None:
    command.upgrade(alembic_config(env["database_url"]), "head")
    _seed_raw_db(env["raw_path"], load_fixture)

    first = _sync("--offline")
    assert first.exit_code == 0, first.output
    assert "Fixture League 2025/2026 (completed season; match list from cache)" in first.output
    assert "teams       3 total: 3 new" in first.output
    assert "matches     3 total: 3 new" in first.output

    second = _sync("--offline")
    assert second.exit_code == 0, second.output
    assert "matches     3 total: 0 new, 0 updated, 3 unchanged" in second.output


@needs_migrations
def test_offline_without_cached_responses_fails_cleanly(env: dict[str, Any]) -> None:
    command.upgrade(alembic_config(env["database_url"]), "head")
    result = _sync("--offline")
    assert result.exit_code == 1
    assert "offline and not cached" in result.output


def test_unmigrated_database_tells_you_to_upgrade(env: dict[str, Any]) -> None:
    result = _sync("--offline")
    assert result.exit_code == 1
    assert "alembic upgrade head" in result.output


@needs_migrations
def test_online_sync_without_api_key_fails_cleanly(env: dict[str, Any]) -> None:
    command.upgrade(alembic_config(env["database_url"]), "head")
    result = _sync()
    assert result.exit_code == 1
    assert "PITCHAPI_API_KEY is not set" in result.output


def test_help_lists_sync_matches() -> None:
    result = runner.invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "matches" in result.output
