"""CLI tests for slice 1d: `sync season`, `sync profiles`, `report formations`,
`report checksum`, offline, against the three-match fixture season from conftest.py."""

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

pytestmark = pytest.mark.skipif(head_revision() is None, reason="no migrations yet")

runner = CliRunner()
T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
SEASON_ARGS = ("--league", "l_FIXT01", "--season", "2025/2026")
LIST_PARAMS = {"season": "2025/2026", "status": "played"}


@pytest.fixture
def seeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, season_responses: dict[str, Any]
) -> None:
    """Migrated scouting.db + raw.db holding the whole fixture season, as if fetched earlier."""
    database_url = f"sqlite:///{tmp_path / 'scouting.db'}"
    raw_path = tmp_path / "raw.db"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RAW_DB_PATH", str(raw_path))
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("PITCHAPI_API_KEY", "")
    command.upgrade(alembic_config(database_url), "head")
    with RawStore(raw_path) as store:
        for path, data in season_responses.items():
            params = LIST_PARAMS if path.endswith("/matches") else {}
            store.put(RawRecord(cache_key(path, params), path, params, 200, None, None, data, T0))


def invoke(*args: str) -> Any:
    return runner.invoke(app, list(args))


def test_sync_season_offline(seeded: None) -> None:
    result = invoke("sync", "season", *SEASON_ARGS, "--offline")
    assert result.exit_code == 0, result.output
    assert "processed 3/3 matches" in result.output
    assert "profiles: 14 players · 14 updated" in result.output
    assert "requests: 0 to PitchAPI, 25 from cache (offline)" in result.output
    assert result.output.count("requests:") == 1
    assert "14/14 players" in result.output


def test_sync_season_limit_then_resume(seeded: None) -> None:
    first = invoke("sync", "season", *SEASON_ARGS, "--offline", "--limit", "2")
    assert first.exit_code == 0, first.output
    assert "processed 2/2 matches" in first.output
    assert "1 matches still to sync" in first.output
    second = invoke("sync", "season", *SEASON_ARGS, "--offline")
    assert "processed 1/1 matches" in second.output


def test_renormalize_keeps_the_checksum(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    before = invoke("report", "checksum").output
    result = invoke("sync", "season", *SEASON_ARGS, "--offline", "--renormalize")
    assert "processed 3/3 matches" in result.output
    assert invoke("report", "checksum").output == before


def test_checksum_tables(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    result = invoke("report", "checksum", "--tables")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines[0]) == 64
    assert any(line.split() == ["match", "3"] for line in lines)


def test_report_formations(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    result = invoke("report", "formations", *SEASON_ARGS)
    assert result.exit_code == 0, result.output
    assert "3 matches" in result.output
    assert "3-4-2-1" in result.output and "4-2-3-1" in result.output
    assert "every starter has a role" in result.output


def test_report_formations_unknown_season_explains(seeded: None) -> None:
    result = invoke("report", "formations", "--league", "l_FIXT01", "--season", "1999/2000")
    assert result.exit_code == 1
    assert "1999/2000" in result.output


def test_sync_profiles_offline(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    result = invoke("sync", "profiles", "--offline")
    assert result.exit_code == 0, result.output
    assert "profiles: 14 players · 0 updated, 14 unchanged" in result.output


# --- aggregates (slice 2a) ------------------------------------------------------------------


def test_complete_season_sync_rebuilds_aggregates(seeded: None) -> None:
    result = invoke("sync", "season", *SEASON_ARGS, "--offline")
    assert "aggregated 2025/2026: 14 players" in result.output
    assert "peer groups (≥ 45 min): GK 4 · FB 2 · AM 2 · ST 2" in result.output
    assert "roles: 10 from starts, 2 from listed position, 2 none" in result.output


def test_partial_sync_leaves_aggregates_alone(seeded: None) -> None:
    result = invoke("sync", "season", *SEASON_ARGS, "--offline", "--limit", "1")
    assert "aggregated" not in result.output
    assert "aggregates not rebuilt: the season isn't fully synced yet" in result.output


def test_aggregate_command_is_repeatable(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    before = invoke("report", "checksum").output
    result = invoke("aggregate", *SEASON_ARGS)
    assert result.exit_code == 0, result.output
    assert "aggregated 2025/2026: 14 players" in result.output
    assert invoke("report", "checksum").output == before


def test_inspect_player_shows_the_season(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    result = invoke("inspect", "player", "p_CST01", *SEASON_ARGS, "--metric", "goals")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert "156 min · 2 matches · 2 starts" in lines[1]
    assert "role: ST (peer group ST, from most minutes as a starter)" in result.output
    goals = next(line.split() for line in lines if line.strip().startswith("goals"))
    assert goals == ["goals", "2", "1.154", "156", "2"]


def test_inspect_player_fractions_and_weighted_rates(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    result = invoke(
        "inspect", "player", "p_CGK01", *SEASON_ARGS,
        "--metric", "passes_completed", "--metric", "distribution_accuracy",
    )  # fmt: skip
    assert "passes_completed" in result.output and "40 / 56" in result.output
    assert "71.4%" in result.output
    assert "weight 60" in result.output and "80%" in result.output


def test_inspect_player_by_match_for_hand_checks(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    result = invoke("inspect", "player", "p_CST01", *SEASON_ARGS, "--metric", "goals", "--matches")
    assert result.exit_code == 0, result.output
    by_match = [line for line in result.output.splitlines() if " min  " in line]
    assert len(by_match) == 2 and all(line.rstrip().endswith("78 min  1") for line in by_match)


def test_inspect_player_ambiguous_name_lists_candidates(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    # The fixture's teams reuse names, so a name can match players on two teams.
    result = invoke("inspect", "player", "STRIKER", *SEASON_ARGS)  # case doesn't matter
    assert result.exit_code == 1
    assert "2 players match 'STRIKER'" in result.output
    lines = result.output.splitlines()
    assert "p_CST01" in lines[1] and "p_AST01" in lines[2]  # most minutes first


def test_inspect_player_before_aggregating_explains(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline", "--limit", "1")
    result = invoke("inspect", "player", "striker", *SEASON_ARGS)
    assert result.exit_code == 1
    assert "run `scouting aggregate`" in result.output


def test_inspect_player_rejects_unknown_metrics(seeded: None) -> None:
    result = invoke("inspect", "player", "x", *SEASON_ARGS, "--metric", "nope")
    assert result.exit_code == 1 and "unknown metric code(s): nope" in result.output
