"""CLI tests for slice 1d: `sync season`, `sync profiles`, `report formations`,
`report checksum`, offline, against the three-match fixture season from conftest.py."""

from __future__ import annotations

import csv
import runpy
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import select, update
from typer.testing import CliRunner

from scouting.adapters.pitchapi.cache import cache_key
from scouting.adapters.pitchapi.raw_store import RawRecord, RawStore
from scouting.cli.main import _ordinal, app
from scouting.config.settings import Settings
from scouting.db.base import make_engine
from scouting.db.models import Metric
from scouting.db.schema_version import alembic_config, head_revision
from scouting.metrics import depth

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
    assert "requests: 0 to PitchAPI, 28 from cache (offline)" in result.output  # 4 per match
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


def test_offline_sync_without_heatmaps_says_how_to_fetch_them(seeded: None, tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "raw.db") as connection:  # as before slice 2d
        connection.execute("DELETE FROM raw_response WHERE path LIKE '%/heatmaps'")
    result = invoke("sync", "season", *SEASON_ARGS, "--offline")
    assert result.exit_code == 0, result.output
    assert "processed 3/3 matches" in result.output
    assert "heatmaps not fetched yet: 3 matches (run the same command without --offline)" in (
        result.output
    )
    assert "aggregated 2025/2026" in result.output  # pending heatmaps don't block aggregates


def test_sync_profiles_offline(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    result = invoke("sync", "profiles", "--offline")
    assert result.exit_code == 0, result.output
    assert "profiles: 14 players · 0 updated, 14 unchanged" in result.output


# --- aggregates (slice 2a) ------------------------------------------------------------------


def test_complete_season_sync_rebuilds_aggregates(seeded: None) -> None:
    result = invoke("sync", "season", *SEASON_ARGS, "--offline")
    assert "aggregated 2025/2026: 14 players" in result.output
    assert "peer groups (≥ 45 min): GK 4 · FB 2 · W 2 · ST 2" in result.output
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
    assert "role: ST (from most minutes as a starter) · peer group ST" in result.output
    assert "6 or 8" not in result.output  # only for central mids
    goals = next(line.split() for line in lines if line.strip().startswith("goals"))
    # Both fixture strikers score 1.154 per 90: tied, so each sits at the midpoint, 50.
    assert goals == ["goals", "2", "1.154", "156", "2", "50th", "(n=2)"]
    assert "percentiles vs 2 STs (≥ 45 min) · small group" in result.output


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


# --- percentiles (slice 2b) --------------------------------------------------------------------


def test_aggregate_reports_percentiles_and_small_groups(seeded: None) -> None:
    result = invoke("sync", "season", *SEASON_ARGS, "--offline")
    assert "percentiles:" in result.output and "across 4 peer groups" in result.output
    assert "small peer group: ST has 2 players (< 20)" in result.output


def test_report_peers(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    result = invoke("report", "peers", *SEASON_ARGS, "--role", "gk", "--metric", "saves")
    assert result.exit_code == 0, result.output
    assert "saves · per 90 · higher is better" in result.output
    assert "GKs with ≥ 45 min: 4 players, 4 ranked on this metric" in result.output
    ranked = [line.split() for line in result.output.splitlines() if " min  " in line]
    assert [row[0] for row in ranked] == ["1", "2", "3", "4"]
    assert ranked[0][-1] == "75.0" and ranked[-1][-1] == "25.0"


def test_report_peers_rejects_unknown_role_and_metric(seeded: None) -> None:
    bad_role = invoke("report", "peers", *SEASON_ARGS, "--role", "LW", "--metric", "saves")
    assert bad_role.exit_code == 1 and "unknown peer role 'LW'" in bad_role.output
    bad_metric = invoke("report", "peers", *SEASON_ARGS, "--role", "ST", "--metric", "x")
    assert bad_metric.exit_code == 1 and "unknown metric code: x" in bad_metric.output


def test_report_thresholds(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    result = invoke("report", "thresholds", *SEASON_ARGS)
    assert result.exit_code == 0, result.output
    passes = next(line.split() for line in result.output.splitlines() if "passes_completed" in line)
    # Every fixture player has far fewer than 200 passes: ranked 0 of those with data.
    assert passes[:2] == ["passes_completed", "200"] and all(c.startswith("0/") for c in passes[2:])
    claims = next(line.split() for line in result.output.splitlines() if "claim_rate" in line)
    assert "-" in claims  # outfield groups have no keeper data


@pytest.mark.parametrize(
    ("n", "expected"),
    [(0, "0th"), (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"),
     (13, "13th"), (21, "21st"), (22, "22nd"), (23, "23rd"), (85, "85th"), (100, "100th"),
     (101, "101st"), (111, "111th")],
)  # fmt: skip
def test_ordinal(n: int, expected: str) -> None:
    assert _ordinal(n) == expected


def test_inspect_player_explains_missing_percentiles(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    keeper = invoke(
        "inspect", "player", "p_CGK01", *SEASON_ARGS, "--metric", "passes_completed"
    ).output
    assert "percentiles vs 4 GKs (≥ 45 min)" in keeper
    assert "< 200 att" in keeper
    sub = invoke("inspect", "player", "p_BGK03", *SEASON_ARGS).output
    assert "percentiles: not ranked (below 45 min)" in sub


def test_aggregate_picks_up_registry_changes(seeded: None) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    engine = make_engine(Settings().database_url)
    with engine.begin() as connection:  # as if the database predates the registry's values
        connection.execute(update(Metric).values(min_attempts=None, direction="higher"))
    invoke("aggregate", *SEASON_ARGS)
    with engine.connect() as connection:
        rows = {
            code: minimum
            for code, minimum in connection.execute(
                select(Metric.code, Metric.min_attempts).where(Metric.min_attempts.is_not(None))
            )
        }
    engine.dispose()
    assert rows["passes_completed"] == 200 and len(rows) == 12


# --- slice 2c: the data-check script on a migrated database ------------------------------------

SCRIPT = Path(__file__).parents[3] / "scripts" / "check_2c.py"


def test_check_2c_script_runs_read_only(
    seeded: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    before = invoke("report", "checksum").output
    monkeypatch.setattr(sys, "argv", ["check_2c.py", *SEASON_ARGS])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    out = capsys.readouterr().out
    assert exit_info.value.code == 0, out
    for heading in ("1. was_fouled", "2. Keeper rates", "3. Role judgement", "4. xA", "5. Is"):
        assert heading in out
    assert invoke("report", "checksum").output == before  # wrote nothing


def test_check_2c_script_runs_selected_checks(
    seeded: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    monkeypatch.setattr(sys, "argv", ["check_2c.py", *SEASON_ARGS, "--only", "5"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    out = capsys.readouterr().out
    assert exit_info.value.code == 0, out
    assert "5. Is /players' expected_assists real xA?" in out
    assert "1. was_fouled" not in out and "4. xA" not in out


def test_check_2c_script_explains_a_missing_season(
    seeded: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["check_2c.py", "--league", "l_NOPE", "--season", "1999"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert exit_info.value.code == 1
    assert "isn't in scouting.db" in capsys.readouterr().err


# --- slices 2d-2 / 2d-2b: report depth --------------------------------------------------------


def midfield_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, min_starts: int = 1) -> str:
    """The synced fixture season with each 4-2-3-1 side's left-back as a DM, striker as a CM
    and keeper standing in as the centre-back (see tests/unit/metrics/test_depth.py): the DM
    plays 23.0 m and the CM 78.75 m in front of him, 55.8 m apart. Re-aggregated, with anchors;
    returns the aggregate output."""
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    with sqlite3.connect(tmp_path / "scouting.db") as connection:
        for suffix, role in (("LB01", "DM"), ("ST01", "CM"), ("GK01", "CB")):
            connection.execute(
                "UPDATE appearance SET role = ? WHERE is_starter AND player_id IN "
                "(SELECT player_id FROM player_source_id WHERE external_id IN (?, ?))",
                (role, f"p_A{suffix}", f"p_C{suffix}"),
            )
        for player, name in (("p_CLB01", "Cleo Deep"), ("p_CST01", "Cyd High")):
            connection.execute(
                "UPDATE player SET name = ?, search_name = ? WHERE id = "
                "(SELECT player_id FROM player_source_id WHERE external_id = ?)",
                (name, name.lower(), player),
            )
    monkeypatch.setattr(depth, "MIN_DEPTH_STARTS", min_starts)  # the fixture has 2 starts each
    monkeypatch.setattr(depth, "MIN_PARTNER_STARTS", min_starts)
    monkeypatch.setattr(depth, "ANCHORS", (("cleo deep", "DM"), ("cyd high", "CM")))
    result = invoke("aggregate", *SEASON_ARGS)
    assert result.exit_code == 0, result.output
    return str(result.output)


def test_report_depth(seeded: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    midfield_fixture(tmp_path, monkeypatch)
    result = invoke("report", "depth", *SEASON_ARGS, "--cut-m", "50")
    assert result.exit_code == 0, result.output
    out = result.output
    assert "reference: the team's starting centre-backs · cut +50.0 m in front of them" in out
    assert "4 central midfielders (DM + CM peer groups, ≥ 45 min); 4 with ≥ 1 central-mid" in out
    cleo = next(line for line in out.splitlines() if line.startswith("  6 ") and "Cleo" in line)
    # CBs, outfield (second opinion), starts, deepest, partner gap, label, anchor check
    assert "+23.0 m" in cleo and "-27.9 m" in cleo and "-55.8 m" in cleo and "✓" in cleo
    assert "the anchors alone would cut at +50.9 m" in out
    assert "with the +50.0 m cut, depth alone: DM 2 · CM 2 · formation fallback 0" in out
    # Aggregation used the agreed 14 m: all four are 8s by depth (23 m and 78.75 m).
    assert "stored groups: DM 0 · CM 4 (by depth 4 · deeper partner 0 · formation 0)" in out
    assert "a trial cut: the stored groups use the agreed one and aren't compared" in out
    assert "  2 m     0    2    2  -  ◄ agreed" in out  # the 6s are the deeper ones: no flips
    assert "6s by depth, largest deeper-partner gap first:" in out


def test_report_depth_partner_flips(
    seeded: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    midfield_fixture(tmp_path, monkeypatch)
    out = invoke("report", "depth", *SEASON_ARGS, "--cut-m", "100").output  # all 6s by depth
    assert "with the +100.0 m cut, depth alone: DM 4 · CM 0" in out
    assert "  2 m     2    2    2  Cal Striker (+55.8 m), Cyd High (+55.8 m)  ◄ agreed" in out


def test_report_depth_checks_the_stored_groups(
    seeded: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    midfield_fixture(tmp_path, monkeypatch)
    out = invoke("report", "depth", *SEASON_ARGS).output  # the agreed 14 m cut
    assert "stored groups match the rule" in out
    cleo = next(line for line in out.splitlines() if line.startswith("  6 ") and "Cleo" in line)
    assert "  CM     CM  ✗ other side of the cut" in cleo  # label, stored group, anchor check
    # Stored groups older than the rule (here: the minimum starts goes back up to 5).
    monkeypatch.setattr(depth, "MIN_DEPTH_STARTS", 5)
    out = invoke("report", "depth", *SEASON_ARGS).output
    assert "4 stored groups differ from the rule" in out and "run `scouting aggregate`" in out


def test_report_depth_everyone(
    seeded: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    midfield_fixture(tmp_path, monkeypatch)
    result = invoke("report", "depth", *SEASON_ARGS, "--cut-m", "50", "--all")
    assert result.exit_code == 0, result.output
    everyone = result.output.split("everyone, deepest first:")[1].splitlines()[2:]
    assert [line.split()[0] for line in everyone] == ["Bea", "Cleo", "Cal", "Cyd"]


def test_report_depth_explains_problems(seeded: None) -> None:
    result = invoke("report", "depth", *SEASON_ARGS)  # not synced, so not aggregated
    assert result.exit_code == 1


# --- slice 2d-3: the stored 6/8 split ---------------------------------------------------------


def six_or_eight(name: str) -> str:
    result = invoke("inspect", "player", name, *SEASON_ARGS, "--metric", "goals")
    assert result.exit_code == 0, result.output
    lines: list[str] = result.output.splitlines()
    return next(line for line in lines if "6 or 8:" in line).strip()


def test_aggregate_reports_the_split(
    seeded: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = midfield_fixture(tmp_path, monkeypatch)
    assert "6/8 split (central mids ≥ 45 min): by depth 4 · deeper partner 0 · formation 0" in out
    assert "peer groups (≥ 45 min): GK" in out and " CM 4 " in out


def test_inspect_player_by_depth(
    seeded: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    midfield_fixture(tmp_path, monkeypatch)
    assert six_or_eight("cleo deep") == (
        "6 or 8: an 8 (CM) by depth: 23.0 m in front of his centre-backs over 2 starts (cut 14 m)"
    )


def test_inspect_player_borderline(
    seeded: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(depth, "DEPTH_CUT", 23.5 / 105)
    midfield_fixture(tmp_path, monkeypatch)
    assert six_or_eight("cleo deep") == (
        "6 or 8: a 6 (DM) by depth: 23.0 m in front of his centre-backs over 2 starts "
        "(cut 23.5 m) · borderline (within 1 m of the cut)"
    )


def test_inspect_player_beside_a_deeper_partner(
    seeded: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(depth, "DEPTH_CUT", 0.9)  # everyone a 6 by depth
    out = midfield_fixture(tmp_path, monkeypatch)
    assert "by depth 2 · deeper partner 2 · formation 0" in out
    assert six_or_eight("cyd high") == (
        "6 or 8: an 8 (CM) beside a deeper partner: 78.8 m in front of his centre-backs over "
        "2 starts, but his deepest fellow central mid played 55.8 m deeper over 2 starts "
        "(≥ 2 m makes an 8)"
    )


def test_inspect_player_by_formation(
    seeded: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    midfield_fixture(tmp_path, monkeypatch, min_starts=5)
    assert six_or_eight("cleo deep") == (
        "6 or 8: a 6 (DM) by formation: 2 central-mid starts with a heatmap (needs 5); "
        "23.0 m in front of his centre-backs over 2 starts"
    )


# --- slice 2e: template options ---------------------------------------------------------------

OPTIONS_SCRIPT = Path(__file__).parents[3] / "scripts" / "template_options.py"


def test_template_options_script_writes_the_csv(
    seeded: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    invoke("sync", "season", *SEASON_ARGS, "--offline")
    before = invoke("report", "checksum").output
    out_file = tmp_path / "options.csv"
    monkeypatch.setattr(sys, "argv", ["template_options.py", *SEASON_ARGS, "--out", str(out_file)])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(OPTIONS_SCRIPT), run_name="__main__")
    out = capsys.readouterr().out
    assert exit_info.value.code == 0, out
    assert "peer groups (≥ 45 min: GK 4 · FB 2 · W 2 · ST 2)" in out
    with out_file.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 4 * len({r["metric"] for r in rows})  # every metric x 4 groups
    goals_st = next(r for r in rows if r["metric"] == "goals" and r["group"] == "ST")
    assert (goals_st["basis"], goals_st["group_size"]) == ("per90", "2")
    saves_st = next(r for r in rows if r["metric"] == "saves" and r["group"] == "ST")
    assert saves_st["ranked"] == "0" and saves_st["median"] == ""  # keepers only
    assert invoke("report", "checksum").output == before  # wrote nothing to scouting.db


def test_template_options_script_explains_a_missing_season(
    seeded: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["template_options.py", "--league", "l_NOPE", "--season", "1999"]
    )
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(OPTIONS_SCRIPT), run_name="__main__")
    assert exit_info.value.code == 1
    assert "isn't in scouting.db" in capsys.readouterr().err
