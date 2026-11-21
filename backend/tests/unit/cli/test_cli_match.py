"""CLI tests for slice 1c: `scouting sync match` and `scouting inspect appearance`, offline,
against temporary databases seeded with the hand-made fixtures."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from typer.testing import CliRunner

from scouting.adapters.pitchapi.cache import cache_key
from scouting.adapters.pitchapi.client import build_path
from scouting.adapters.pitchapi.raw_store import RawRecord, RawStore
from scouting.cli.main import app
from scouting.db.schema_version import alembic_config, head_revision

pytestmark = pytest.mark.skipif(head_revision() is None, reason="no migrations yet")

runner = CliRunner()
T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
MATCH = "m_FIXT01"
RESPONSES: tuple[tuple[str, dict[str, str], str], ...] = (
    (build_path("leagues"), {}, "leagues.json"),
    (
        build_path("leagues", "l_FIXT01", "matches"),
        {"season": "2025/2026", "status": "played"},
        "match_list.json",
    ),
    (build_path("matches", MATCH, "players"), {}, "match_players.json"),
    (build_path("matches", MATCH, "advanced", "players"), {}, "match_advanced.json"),
    (build_path("matches", MATCH, "lineups"), {}, "match_lineups.json"),
)


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, load_fixture: Any) -> None:
    """Migrated scouting.db + raw.db holding every response, as if fetched earlier."""
    database_url = f"sqlite:///{tmp_path / 'scouting.db'}"
    raw_path = tmp_path / "raw.db"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("RAW_DB_PATH", str(raw_path))
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("PITCHAPI_API_KEY", "")
    command.upgrade(alembic_config(database_url), "head")
    with RawStore(raw_path) as store:
        for path, params, name in RESPONSES:
            store.put(
                RawRecord(
                    cache_key(path, params), path, params, 200, None, None, load_fixture(name), T0
                )
            )


def invoke(*args: str) -> Any:
    return runner.invoke(app, list(args))


def test_sync_match_offline(seeded: None) -> None:
    assert invoke("sync", "matches", "--league", "l_FIXT01", "--season", "2025/2026",
                  "--offline").exit_code == 0  # fmt: skip
    result = invoke("sync", "match", MATCH, "--offline")
    assert result.exit_code == 0, result.output
    assert "Example Athletic v Sample Rovers · 2025-08-15" in result.output
    assert "data: players ok, advanced ok, lineups ok" in result.output
    assert "appearances    7 total: 7 new" in result.output
    assert "5 starters, 3 goalkeepers" in result.output
    assert "0 to PitchAPI, 3 from cache (offline)" in result.output


def test_sync_match_before_match_list_explains(seeded: None) -> None:
    result = invoke("sync", "match", MATCH, "--offline")
    assert result.exit_code == 1
    assert "sync matches" in result.output


def test_inspect_shows_stored_and_raw_values(seeded: None) -> None:
    invoke("sync", "matches", "--league", "l_FIXT01", "--season", "2025/2026", "--offline")
    invoke("sync", "match", MATCH, "--offline")
    result = invoke("inspect", "appearance", "--match", MATCH, "--player", "p_FXLB01")
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Bea Fullbäck · Example Athletic v Sample Rovers · 2025-08-15" in out
    assert "starter · 4-2-3-1 slot 38 → FB (L) · outfield" in out
    assert "minutes 90 (actual 97) · rating 6.8" in out
    lines = {line.split()[0]: line for line in out.splitlines()[4:] if line.strip()}
    assert lines["shots"].split()[1:3] == ["0", "(absent)"]  # absent → stored 0
    assert lines["xgot"].split()[1:3] == ["NULL", "(absent)"]  # absent → stored NULL
    assert lines["was_fouled"].split()[1:3] == ["NULL", "null"]  # provider null kept
    assert "1 / 3" in lines["crosses_completed"]
    assert "players: accurate_crosses" in lines["crosses_completed"]
    assert "advanced: passing.key_passes" in lines["key_passes"]
    assert "saves" not in lines  # outfielder: no keeper metrics


def test_inspect_unknown_appearance_explains(seeded: None) -> None:
    result = invoke("inspect", "appearance", "--match", MATCH, "--player", "p_FXUN01")
    assert result.exit_code == 1
    assert "no appearance" in result.output


# --- heatmaps and depth (slice 2d) --------------------------------------------------------------


def add_heatmaps(load_fixture: Any) -> None:
    path = build_path("matches", MATCH, "heatmaps")
    data = load_fixture("match_heatmaps.json")
    with RawStore(os.environ["RAW_DB_PATH"]) as store:
        store.put(RawRecord(cache_key(path), path, {}, 200, None, None, data, T0))


def sync_all() -> None:
    invoke("sync", "matches", "--league", "l_FIXT01", "--season", "2025/2026", "--offline")
    invoke("sync", "match", MATCH, "--offline")


def test_heatmaps_not_fetched_yet_are_pending_offline(seeded: None) -> None:
    invoke("sync", "matches", "--league", "l_FIXT01", "--season", "2025/2026", "--offline")
    result = invoke("sync", "match", MATCH, "--offline")
    assert result.exit_code == 0, result.output
    assert "lineups ok, heatmaps pending" in result.output
    out = invoke("inspect", "appearance", "--match", MATCH, "--player", "p_FXLB01").output
    assert "depth not fetched yet (run an online sync)" in out


def test_inspect_shows_depth(seeded: None, load_fixture: Any) -> None:
    add_heatmaps(load_fixture)
    sync_all()
    lb = invoke("inspect", "appearance", "--match", MATCH, "--player", "p_FXLB01")
    assert lb.exit_code == 0, lb.output
    assert "depth 0.312 (0 = own goal line, 1 = opponent's) from 8 actions" in lb.output
    keeper = invoke("inspect", "appearance", "--match", MATCH, "--player", "p_FXGK03").output
    assert "depth none (no actions on the heatmap)" in keeper


def test_sync_match_reports_players_without_a_heatmap(seeded: None, load_fixture: Any) -> None:
    path = build_path("matches", MATCH, "heatmaps")
    data = load_fixture("match_heatmaps.json")
    data["players"] = [r for r in data["players"] if r["player"]["id"] != "p_FXSB01"]
    with RawStore(os.environ["RAW_DB_PATH"]) as store:
        store.put(RawRecord(cache_key(path), path, {}, 200, None, None, data, T0))
    invoke("sync", "matches", "--league", "l_FIXT01", "--season", "2025/2026", "--offline")
    result = invoke("sync", "match", MATCH, "--offline")
    assert "lineups ok, heatmaps ok" in result.output
    assert "no heatmap for: Dev Sub" in result.output
    out = invoke("inspect", "appearance", "--match", MATCH, "--player", "p_FXSB01").output
    assert "depth none (player not on the heatmap)" in out
