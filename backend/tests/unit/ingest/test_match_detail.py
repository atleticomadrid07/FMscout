"""Unit tests for the per-match sync (slice 1c): appearances, roles, goalkeepers, absence
policies, `applies_to`, idempotency, missing endpoints, and error cases.

Uses the hand-made fixture match m_FIXT01 (tests/fixtures/pitchapi/match_*.json):
  home Example Athletic, 4-2-3-1: GK Ada (11), LB Bea (38), ST Cal (115),
       sub Dev (MID, 12'), unused sub Eli
  away Sample Rovers, 3-4-2-1:    GK Fay (11), 10 Gus (84), sub keeper Hal (5', not rated)
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.raw_store import RawRecord
from scouting.adapters.pitchapi.schemas import LeaguesPayload, MatchListPayload
from scouting.db.models import (
    Appearance,
    FormationRole,
    Match,
    Metric,
    Player,
    PlayerSourceId,
    StatValue,
)
from scouting.domain.positions import FORMATION_ROLES
from scouting.ingest.match_detail import MatchDetailSummary, find_match, normalize_match
from scouting.ingest.match_list import SyncError, normalize_match_list
from scouting.metrics.registry import REGISTRY

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
MATCH = "m_FIXT01"
OUTFIELD_METRICS = sum(1 for m in REGISTRY if m.applies_to != "gk")
GK_METRICS = sum(1 for m in REGISTRY if m.applies_to != "outfield")
ADVANCED_OUTFIELD = sum(
    1 for m in REGISTRY if m.source == "pitchapi_advanced" and m.applies_to != "gk"
)
ADVANCED_GK = sum(
    1 for m in REGISTRY if m.source == "pitchapi_advanced" and m.applies_to != "outfield"
)
# Metrics read from the advanced `goalkeeping` group, which is null for sub keepers (Hal).
GOALKEEPING_GROUP = sum(1 for m in REGISTRY if m.source_key.startswith("goalkeeping."))
SUB_KEEPER_METRICS = GK_METRICS - GOALKEEPING_GROUP


def record(
    endpoint: str, data: Any = None, *, error: str | None = None, at: datetime = T0
) -> RawRecord:
    return RawRecord(
        cache_key=f"GET /v1/matches/{MATCH}/{endpoint}",
        path=f"/v1/matches/{MATCH}/{endpoint}",
        params={},
        status=404 if error else 200,
        error_code=error,
        error_message="msg" if error else None,
        data=None if error else data,
        fetched_at=at,
    )


@pytest.fixture
def match_db(db_engine: Engine, load_fixture: Any) -> Engine:
    """scouting.db with the fixture league, season, teams and matches already synced (1b)."""
    league = LeaguesPayload.model_validate(load_fixture("leagues.json")).leagues[0]
    payload = MatchListPayload.model_validate(load_fixture("match_list.json"))
    with Session(db_engine) as session, session.begin():
        normalize_match_list(session, league, payload, fetched_at=T0)
    return db_engine


Normalize = Callable[..., MatchDetailSummary]


@pytest.fixture
def normalize(match_db: Engine, load_fixture: Any) -> Normalize:
    """Normalize m_FIXT01; any endpoint can be overridden with data or a RawRecord."""

    def run(**overrides: Any) -> MatchDetailSummary:
        records = {
            "players": record("players", load_fixture("match_players.json")),
            "advanced": record("advanced/players", load_fixture("match_advanced.json")),
            "lineups": record("lineups", load_fixture("match_lineups.json")),
        }
        for name, value in overrides.items():
            if isinstance(value, RawRecord):
                records[name] = value
            else:
                path = "advanced/players" if name == "advanced" else name
                records[name] = record(path, value)
        with Session(match_db) as session, session.begin():
            return normalize_match(session, find_match(session, MATCH), **records)

    return run


def appearance(engine: Engine, player_external_id: str) -> Appearance:
    with Session(engine) as session:
        return session.scalars(
            select(Appearance)
            .join(PlayerSourceId, PlayerSourceId.player_id == Appearance.player_id)
            .where(PlayerSourceId.external_id == player_external_id)
        ).one()


def stats(engine: Engine, player_external_id: str) -> dict[str, tuple[float | None, float | None]]:
    """metric code → (value, denominator) for one player in the fixture match."""
    app_id = appearance(engine, player_external_id).id
    with Session(engine) as session:
        rows = session.execute(
            select(Metric.code, StatValue.value, StatValue.denominator)
            .join(Metric, Metric.id == StatValue.metric_id)
            .where(StatValue.appearance_id == app_id)
        ).all()
    return {code: (value, denominator) for code, value, denominator in rows}


def count(engine: Engine, model: type[Any]) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


# --- appearances, roles, goalkeepers ------------------------------------------------------------


def test_appearances_only_for_players_with_minutes(match_db: Engine, normalize: Normalize) -> None:
    summary = normalize()
    assert summary.appearances.inserted == 7  # Eli (unused sub) has no appearance
    assert count(match_db, Appearance) == 7
    assert count(match_db, Player) == 7
    assert (summary.starters, summary.goalkeepers) == (5, 3)
    assert summary.unmapped_slots == [] and summary.unknown_keys == set()


@pytest.mark.parametrize(
    ("player", "starter", "slot", "sub", "role", "side", "formation"),
    [
        ("p_FXGK01", True, 11, None, "GK", "C", "4-2-3-1"),
        ("p_FXLB01", True, 38, None, "FB", "L", "4-2-3-1"),
        ("p_FXST01", True, 115, None, "ST", "C", "4-2-3-1"),
        ("p_FXAM01", True, 84, None, "AM", "R", "3-4-2-1"),
        ("p_FXSB01", False, None, 2, None, None, "4-2-3-1"),  # subs get no detailed role
        ("p_FXGK03", False, None, 0, None, None, "3-4-2-1"),
    ],
)
def test_lineup_fields(
    match_db: Engine,
    normalize: Normalize,
    player: str,
    starter: bool,
    slot: int | None,
    sub: int | None,
    role: str | None,
    side: str | None,
    formation: str,
) -> None:
    normalize()
    a = appearance(match_db, player)
    assert (a.is_starter, a.slot_code, a.sub_category) == (starter, slot, sub)
    assert (a.role, a.side, a.formation) == (role, side, formation)


@pytest.mark.parametrize(
    ("player", "is_gk"),
    [("p_FXGK01", True), ("p_FXGK02", True), ("p_FXGK03", True), ("p_FXLB01", False),
     ("p_FXSB01", False)],
)  # fmt: skip
def test_goalkeeper_rule(match_db: Engine, normalize: Normalize, player: str, is_gk: bool) -> None:
    normalize()  # GK01/GK02 by slot 11, GK03 by sub category 0
    assert appearance(match_db, player).is_goalkeeper is is_gk


def test_minutes_rating_and_actual_minutes(match_db: Engine, normalize: Normalize) -> None:
    normalize()
    striker = appearance(match_db, "p_FXST01")
    assert (striker.minutes, striker.minutes_actual, striker.rating) == (78, 80, 7.9)
    backup = appearance(match_db, "p_FXGK03")
    assert backup.minutes == 5 and backup.rating is None  # short cameo: not rated, not 0


def test_match_row_is_updated(match_db: Engine, normalize: Normalize) -> None:
    normalize(advanced=record("advanced/players", None, error="ANALYTICS_UNAVAILABLE",
                              at=T0 + timedelta(hours=1)))  # fmt: skip
    with Session(match_db) as session:
        match = find_match(session, MATCH)
        assert (match.home_formation, match.away_formation) == ("4-2-3-1", "3-4-2-1")
        assert (match.players_status, match.advanced_status, match.lineups_status) == (
            "ok",
            "unavailable",
            "ok",
        )
        assert match.normalized_at == (T0 + timedelta(hours=1)).replace(tzinfo=None)


def test_player_search_name_is_accent_free(match_db: Engine, normalize: Normalize) -> None:
    normalize()
    with Session(match_db) as session:
        names = set(session.scalars(select(Player.search_name)))
    assert "bea fullback" in names  # "Bea Fullbäck"


# --- stat values: absence policies and applies_to ----------------------------------------------


def test_stat_value_counts_follow_applies_to(match_db: Engine, normalize: Normalize) -> None:
    summary = normalize()
    assert summary.stat_values == 4 * OUTFIELD_METRICS + 2 * GK_METRICS + SUB_KEEPER_METRICS
    assert len(stats(match_db, "p_FXST01")) == OUTFIELD_METRICS
    assert len(stats(match_db, "p_FXGK01")) == GK_METRICS
    assert len(stats(match_db, "p_FXGK03")) == SUB_KEEPER_METRICS  # no goalkeeping group


def test_keepers_get_no_outfield_rows_and_outfielders_no_saves(
    match_db: Engine, normalize: Normalize
) -> None:
    normalize()
    keeper, fullback = stats(match_db, "p_FXGK01"), stats(match_db, "p_FXLB01")
    assert keeper["saves"] == (3.0, None)
    assert "goals" not in keeper and "shots" not in keeper and "xg" not in keeper
    assert "saves" not in fullback
    assert stats(match_db, "p_FXGK03")["saves"] == (0.0, None)  # present 0, not absent


def test_absence_policies_on_the_fullback(match_db: Engine, normalize: Normalize) -> None:
    normalize()
    s = stats(match_db, "p_FXLB01")
    assert s["shots"] == (0.0, None)  # key absent, "zero" → 0
    assert s["xg"] == (0.0, None)
    assert s["xgot"] == (None, None)  # key absent, "null" → NULL
    assert s["was_fouled"] == (None, None)  # provider null stays NULL
    assert s["crosses_completed"] == (1.0, 3.0)  # fraction present
    assert s["dribbles_completed"] == (0.0, 0.0)  # fraction absent → 0 of 0
    assert s["passes_completed"] == (40.0, 50.0)
    assert s["tackles"] == (2.0, None)


def test_present_values_on_the_striker(match_db: Engine, normalize: Normalize) -> None:
    normalize()
    s = stats(match_db, "p_FXST01")
    assert s["goals"] == (1.0, None) and s["shots"] == (3.0, None)
    assert s["shots_on_target"] == (2.0, None)
    assert s["xg"] == (0.84, None) and s["npxg"] == (0.84, None) and s["xgot"] == (0.91, None)
    assert s["xa"] == (0.0, None)  # absent → 0
    assert s["dribbles_completed"] == (2.0, 4.0)
    assert s["was_fouled"] == (2.0, None)


def test_advanced_metrics(match_db: Engine, normalize: Normalize) -> None:
    normalize()
    s = stats(match_db, "p_FXAM01")
    assert s["key_passes"] == (2.0, None)
    assert s["progressive_passes"] == (4.0, None)
    assert s["progressive_carries"] == (5.0, None)
    assert s["xt"] == (0.33, None)


def test_two_field_fractions_and_breakdowns(match_db: Engine, normalize: Normalize) -> None:
    normalize()
    ten = stats(match_db, "p_FXAM01")
    assert ten["take_ons_completed"] == (3.0, 5.0)  # take_ons_won of take_ons
    assert ten["sca_pass_live"] == (2.0, None) and ten["sca_pass_dead"] == (1.0, None)
    assert ten["sca_take_on"] == (0.0, None)  # breakdown key missing → 0 (spike-verified)
    assert stats(match_db, "p_FXLB01")["take_ons_completed"] == (0.0, 0.0)  # both absent


def test_keeper_rates_are_stored_weighted(match_db: Engine, normalize: Normalize) -> None:
    normalize()
    ada, fay = stats(match_db, "p_FXGK01"), stats(match_db, "p_FXGK02")
    # rate x weight over weight, so season sums give the exact weighted average
    assert ada["distribution_accuracy"] == (80.0 * 30, 30.0)
    assert ada["avg_pass_length"] == (31.5 * 30, 30.0)
    assert ada["claims_won"] == (2.0, 2.0)
    assert fay["claim_rate"] == (0.0, 0.0)  # no claims: rate undefined, contributes nothing
    assert fay["claims_won"] == (0.0, 0.0)
    assert ada["distributions"] == (30.0, None) and ada["launches"] == (6.0, None)


def test_sub_keeper_gets_no_advanced_goalkeeping_rows(
    match_db: Engine, normalize: Normalize
) -> None:
    normalize()
    hal = stats(match_db, "p_FXGK03")
    assert "distributions" not in hal and "claim_rate" not in hal
    assert hal["saves"] == (0.0, None)  # /players keeper stats still there
    assert hal["sweeper_actions"] == (0.0, None)


# --- missing endpoints --------------------------------------------------------------------------


def test_analytics_unavailable_keeps_appearances_without_advanced_rows(
    match_db: Engine, normalize: Normalize
) -> None:
    summary = normalize(advanced=record("advanced/players", error="ANALYTICS_UNAVAILABLE"))
    assert summary.advanced_status == "unavailable"
    assert summary.appearances.inserted == 7
    assert summary.stat_values == 4 * (OUTFIELD_METRICS - ADVANCED_OUTFIELD) + 3 * (
        GK_METRICS - ADVANCED_GK
    )
    assert "xt" not in stats(match_db, "p_FXST01")  # no row: no exposure for advanced metrics
    assert appearance(match_db, "p_FXST01").minutes_actual is None


def test_missing_lineups_fall_back_to_keeper_stats(match_db: Engine, normalize: Normalize) -> None:
    summary = normalize(lineups=record("lineups", error="RESOURCE_NOT_FOUND"))
    assert summary.lineups_status == "missing"
    keeper, fullback = appearance(match_db, "p_FXGK01"), appearance(match_db, "p_FXLB01")
    assert keeper.is_goalkeeper and not fullback.is_goalkeeper  # via the `saves` key
    assert keeper.is_starter is None and keeper.role is None


def test_missing_players_endpoint_writes_no_appearances(
    match_db: Engine, normalize: Normalize
) -> None:
    summary = normalize(players=record("players", error="RESOURCE_NOT_FOUND"))
    assert summary.players_status == "missing"
    assert count(match_db, Appearance) == 0


def test_player_missing_from_advanced_is_reported(
    match_db: Engine, normalize: Normalize, load_fixture: Any
) -> None:
    advanced = load_fixture("match_advanced.json")
    advanced["players"] = [r for r in advanced["players"] if r["player"]["id"] != "p_FXST01"]
    summary = normalize(advanced=advanced)
    assert summary.missing_advanced_rows == ["Cal Striker"]
    assert "xt" not in stats(match_db, "p_FXST01")


def test_unmapped_formation_gives_null_role_and_is_reported(
    match_db: Engine, normalize: Normalize, load_fixture: Any, caplog: pytest.LogCaptureFixture
) -> None:
    lineups = load_fixture("match_lineups.json")
    lineups["home"]["formation"] = "4-2-4"
    with caplog.at_level(logging.DEBUG):  # per match at DEBUG; the CLI summary reports it
        summary = normalize(lineups=lineups)
    assert ("4-2-4", 38) in summary.unmapped_slots
    fullback = appearance(match_db, "p_FXLB01")
    assert fullback.role is None and fullback.slot_code == 38
    assert fullback.is_goalkeeper is False
    assert "no role mapping for formation 4-2-4" in caplog.text


def test_unknown_stat_key_is_reported_not_ingested(
    match_db: Engine, normalize: Normalize, load_fixture: Any
) -> None:
    players = load_fixture("match_players.json")
    players[2]["stats"][0]["stats"]["New stat"] = {
        "key": "brand_new_metric",
        "stat": {"type": "integer", "value": 4},
    }
    assert normalize(players=players).unknown_keys == {"brand_new_metric"}


# --- idempotency --------------------------------------------------------------------------------


def test_running_twice_gives_identical_database(
    match_db: Engine, normalize: Normalize, snapshot: Any
) -> None:
    normalize()
    before = snapshot(match_db)
    summary = normalize()
    assert snapshot(match_db) == before
    assert (summary.appearances.inserted, summary.appearances.updated) == (0, 0)
    assert summary.appearances.unchanged == 7


def test_corrected_value_updates_in_place(
    match_db: Engine, normalize: Normalize, load_fixture: Any
) -> None:
    normalize()
    players = load_fixture("match_players.json")
    players[2]["stats"][0]["stats"]["Goals"]["stat"]["value"] = 2
    normalize(players=players)
    assert stats(match_db, "p_FXST01")["goals"] == (2.0, None)
    assert count(match_db, Appearance) == 7


def test_player_removed_by_provider_loses_appearance(
    match_db: Engine, normalize: Normalize, load_fixture: Any
) -> None:
    normalize()
    players = [p for p in load_fixture("match_players.json") if p["player"]["id"] != "p_FXSB01"]
    summary = normalize(players=players)
    assert summary.appearances_removed == 1
    assert count(match_db, Appearance) == 6
    assert count(match_db, StatValue) == 3 * OUTFIELD_METRICS + 2 * GK_METRICS + SUB_KEEPER_METRICS


def test_reference_data_is_synced(match_db: Engine, normalize: Normalize) -> None:
    normalize()
    normalize()
    assert count(match_db, Metric) == len(REGISTRY)
    assert count(match_db, FormationRole) == 11 * len(FORMATION_ROLES)


# --- errors -------------------------------------------------------------------------------------


def test_conflicting_duplicate_key_raises(
    match_db: Engine, normalize: Normalize, load_fixture: Any
) -> None:
    players = load_fixture("match_players.json")
    players[1]["stats"][2]["stats"]["Defensive actions"]["stat"]["value"] = 6  # top_stats says 5
    with pytest.raises(SyncError, match="conflicting values"):
        normalize(players=players)


def test_unknown_stat_type_raises(
    match_db: Engine, normalize: Normalize, load_fixture: Any
) -> None:
    players = load_fixture("match_players.json")
    players[2]["stats"][0]["stats"]["Goals"]["stat"]["type"] = "percentile"
    with pytest.raises(SyncError, match="unknown stat type"):
        normalize(players=players)


def test_wrong_type_for_a_registry_metric_raises(
    match_db: Engine, normalize: Normalize, load_fixture: Any
) -> None:
    players = load_fixture("match_players.json")
    players[2]["stats"][0]["stats"]["Goals"]["stat"] = {
        "type": "fractionWithPercentage",
        "value": 1,
        "total": 2,
    }
    with pytest.raises(SyncError, match="expected integer/double"):
        normalize(players=players)


def test_team_not_in_match_raises(
    match_db: Engine, normalize: Normalize, load_fixture: Any
) -> None:
    players = load_fixture("match_players.json")
    players[0]["team_id"] = "t_FIXTC3"  # a real fixture team, but not playing in m_FIXT01
    with pytest.raises(SyncError, match="isn't one of this match's teams"):
        normalize(players=players)


def test_malformed_lineups_raise(match_db: Engine, normalize: Normalize, load_fixture: Any) -> None:
    lineups = copy.deepcopy(load_fixture("match_lineups.json"))
    del lineups["home"]["starters"][0]["player_id"]
    with pytest.raises(SyncError, match="unexpected response shape"):
        normalize(lineups=lineups)


def test_error_rolls_back_everything(
    match_db: Engine, normalize: Normalize, load_fixture: Any
) -> None:
    players = load_fixture("match_players.json")
    players[-1]["team_id"] = "t_FIXTC3"  # fails on the last player
    with pytest.raises(SyncError):
        normalize(players=players)
    assert count(match_db, Appearance) == 0 and count(match_db, Player) == 0


def test_unknown_match_raises(match_db: Engine) -> None:
    with Session(match_db) as session, pytest.raises(SyncError, match="sync matches"):
        find_match(session, "m_NOPE00")


def test_match_not_in_db_check_runs_before_fetching(match_db: Engine, normalize: Normalize) -> None:
    with Session(match_db) as session:
        assert session.scalar(select(func.count()).select_from(Match)) == 3
