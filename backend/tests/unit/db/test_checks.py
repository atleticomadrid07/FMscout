"""Slice 2c data checks on the three-match fixture season from conftest.py.

Fixture facts the tests lean on: per match, the 4-2-3-1 side's outfield players have
was_fouled NULL (left-back), 2 (striker) and 1 (sub); the 3-4-2-1 side's AM has 3; nobody
has a fouls key, so fouls_committed is 0 everywhere. Keepers: 20% launches (6 of 30) and
40% (10 of 25); claims 2 of 2 (100%) and 0 of 0 (rate NULL). Tests edit stored values to
create the case they need.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from scouting.db.checks import (
    RATE_SPECS,
    SLOT_SPECS,
    CheckError,
    SlotSpec,
    check_rate,
    check_slots,
    check_was_fouled,
    check_xa_definition,
    check_xa_xag,
)
from scouting.db.models import Appearance, MatchSourceId, Metric, PlayerSourceId, StatValue
from scouting.ingest.season import find_season
from scouting.metrics.aggregate import aggregate_season

LEAGUE, SEASON = "l_FIXT01", "2025/2026"
SCRIPT = Path(__file__).parents[3] / "scripts" / "check_2c.py"
LAUNCH_PCT, CLAIM_RATE = RATE_SPECS


def season_id(session: Session) -> int:
    return find_season(session, LEAGUE, SEASON).id


def aggregate(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        aggregate_season(session, season_id(session))


def appearance_id(session: Session, player: str, match: str) -> int:
    return session.scalars(
        select(Appearance.id)
        .join(PlayerSourceId, PlayerSourceId.player_id == Appearance.player_id)
        .join(MatchSourceId, MatchSourceId.match_id == Appearance.match_id)
        .where(PlayerSourceId.external_id == player, MatchSourceId.external_id == match)
    ).one()


def set_value(
    engine: Engine, player: str, match: str, code: str, value: float | None,
    denominator: float | None = None,
) -> None:  # fmt: skip
    with Session(engine) as session, session.begin():
        metric_id = session.scalars(select(Metric.id).where(Metric.code == code)).one()
        result = session.execute(
            update(StatValue)
            .where(
                StatValue.appearance_id == appearance_id(session, player, match),
                StatValue.metric_id == metric_id,
            )
            .values(value=value, denominator=denominator)
        )
        assert result.rowcount == 1  # type: ignore[attr-defined]


def set_slot(engine: Engine, player: str, match: str, formation: str, slot: int) -> None:
    with Session(engine) as session, session.begin():
        session.execute(
            update(Appearance)
            .where(Appearance.id == appearance_id(session, player, match))
            .values(formation=formation, slot_code=slot, is_starter=True)
        )


# --- 1. was_fouled ------------------------------------------------------------------------------


def test_was_fouled_counts_values_and_gaps(synced_season: Engine) -> None:
    with Session(synced_season) as session:
        check = check_was_fouled(session, season_id(session))
    # 3 matches x 2 teams. Each side's known was_fouled is 3 (2 + 1, or the AM's 3) and the
    # opponent committed 0, so every gap is -3.
    assert check.team_matches == 6 and check.one_sided == 0
    assert check.gaps == {-3: 6} and check.exact == 0 and check.within_one == 0
    assert (check.opponent_fouls, check.known_fouled) == (0, 18)
    # Keepers have no was_fouled row: 4 outfield rows per match.
    assert check.values == {None: 3, 1.0: 3, 2.0: 3, 3.0: 3}
    buckets = {b.label: (b.team_matches, b.mean_gap, b.exact) for b in check.buckets}
    assert buckets["0"] == (3, -3.0, 0)  # the 3-4-2-1 side: no NULLs
    assert buckets["1-3"] == (3, -3.0, 0)  # the 4-2-3-1 side: the left-back's NULL
    assert buckets["10+"] == (0, None, 0)


def test_was_fouled_matches_opponent_fouls(synced_season: Engine) -> None:
    # m_FIXT01 is A (4-2-3-1) v B (3-4-2-1). B commits 3 fouls on A's players, A commits 3
    # on B's AM: both team-matches now balance, with A's NULL read as 0.
    set_value(synced_season, "p_BAM01", "m_FIXT01", "fouls_committed", 3)
    set_value(synced_season, "p_AST01", "m_FIXT01", "fouls_committed", 2)
    set_value(synced_season, "p_ASB01", "m_FIXT01", "fouls_committed", 1)
    with Session(synced_season) as session:
        check = check_was_fouled(session, season_id(session))
    assert check.exact == 2 and check.gaps == {-3: 4, 0: 2}
    assert check.within_one == 2


def test_was_fouled_gap_grows_with_hidden_fouls(synced_season: Engine) -> None:
    # If B committed 5 on A but A's known was_fouled is 3, the gap (+2) sits with A, the
    # side whose left-back is NULL: the pattern "NULL hides fouls" would produce.
    set_value(synced_season, "p_BAM01", "m_FIXT01", "fouls_committed", 5)
    with Session(synced_season) as session:
        check = check_was_fouled(session, season_id(session))
    assert check.gaps == {-3: 5, 2: 1}
    nulls = next(b for b in check.buckets if b.label == "1-3")
    assert nulls.mean_gap == pytest.approx((2 - 3 - 3) / 3)


def test_was_fouled_counts_null_fouls_rows_as_zero(synced_season: Engine) -> None:
    set_value(synced_season, "p_BAM01", "m_FIXT01", "fouls_committed", None)
    with Session(synced_season) as session:
        check = check_was_fouled(session, season_id(session))
    assert check.fouls_null_rows == 1 and check.gaps == {-3: 6}


def test_checks_need_the_metrics(db_engine: Engine) -> None:
    with Session(db_engine) as session, pytest.raises(CheckError, match=r"not in scouting\.db"):
        check_was_fouled(session, 1)


# --- 2. keeper rates ------------------------------------------------------------------------------


def test_launch_pct_equals_launches_over_distributions(synced_season: Engine) -> None:
    with Session(synced_season) as session:
        check = check_rate(session, season_id(session), LAUNCH_PCT)
    # Two starting keepers per match; the sub keeper has no goalkeeping data (no rows).
    assert (check.rows_with_rate, check.compared, check.no_attempts, check.missing) == (6, 6, 0, 0)
    assert check.scale == 100 and check.tolerance == 0.5
    assert check.within_tolerance == 6
    assert check.max_abs_diff == pytest.approx(0)


def test_launch_pct_reports_the_largest_differences(synced_season: Engine) -> None:
    # 25% sent where the counts say 6 of 30 = 20%. Stored as rate x weight (D-012).
    set_value(synced_season, "p_AGK01", "m_FIXT01", "launch_pct", 25 * 30, 30)
    with Session(synced_season) as session:
        check = check_rate(session, season_id(session), LAUNCH_PCT)
    assert check.within_tolerance == 5
    assert check.max_abs_diff == pytest.approx(5)
    assert check.mean_diff == pytest.approx(5 / 6)
    worst = check.worst[0]
    assert (worst.player, worst.attempts) == ("Ada Keeper", 30)
    assert (worst.sent, worst.from_counts) == pytest.approx((25, 20))
    assert "Example Athletic v Sample Rovers" in worst.match_label


def test_claim_rate_skips_keepers_without_claims(synced_season: Engine) -> None:
    with Session(synced_season) as session:
        check = check_rate(session, season_id(session), CLAIM_RATE)
    # 2 of 2 claims (100%) for the 4-2-3-1 keeper; 0 claims for the other: nothing to compare.
    assert (check.compared, check.no_attempts, check.within_tolerance) == (3, 3, 3)


def test_rate_check_counts_unknown_rates(synced_season: Engine) -> None:
    set_value(synced_season, "p_AGK01", "m_FIXT01", "launch_pct", None)
    with Session(synced_season) as session:
        check = check_rate(session, season_id(session), LAUNCH_PCT)
    assert (check.compared, check.missing) == (5, 1)


# --- 3. role judgement calls ---------------------------------------------------------------------


def test_slots_list_who_started_there(synced_season: Engine) -> None:
    aggregate(synced_season)
    with Session(synced_season) as session:
        group = check_slots(session, season_id(session), SlotSpec("3-4-2-1", (84, 86)))
    assert (group.label, group.mapped_role, group.starts) == ("3-4-2-1 slots 84/86", "W", 3)
    first, second = group.players
    # B's AM started there twice (m_FIXT01, m_FIXT02), A's once (m_FIXT03).
    assert (first.team, first.starts, first.minutes, first.breakdown) == (
        "Sample Rovers", 2, 180, "84 x2",
    )  # fmt: skip
    assert (first.primary_role, first.peer_role, first.season_minutes) == ("W", "W", 180)
    assert (second.team, second.starts) == ("Example Athletic", 1)


def test_slots_filter_by_formation(synced_season: Engine) -> None:
    # A's left-back moved to 64 in a 4-2-3-1 (m_FIXT01), C's to 64 in a 4-3-3 (m_FIXT02).
    set_slot(synced_season, "p_ALB01", "m_FIXT01", "4-2-3-1", 64)
    set_slot(synced_season, "p_CLB01", "m_FIXT02", "4-3-3", 64)
    with Session(synced_season) as session:
        group = check_slots(session, season_id(session), SLOT_SPECS[0])
    assert (group.mapped_role, group.starts) == ("DM", 1)
    (player,) = group.players
    assert (player.team, player.breakdown) == ("Example Athletic", "64 x1")
    assert player.season_minutes is None  # not aggregated in this test


def test_shared_slot_groups_by_formation_and_skips_unmapped(synced_season: Engine) -> None:
    set_slot(synced_season, "p_CST01", "m_FIXT02", "4-4-1-1", 95)
    set_slot(synced_season, "p_CST01", "m_FIXT03", "3-5-1-1", 95)
    set_slot(synced_season, "p_AST01", "m_FIXT01", "9-9-9", 95)  # unmapped: no role came of it
    with Session(synced_season) as session:
        group = check_slots(session, season_id(session), SLOT_SPECS[2])
    assert (group.label, group.mapped_role, group.starts) == ("slot 95, any formation", "AM", 2)
    (player,) = group.players
    assert player.breakdown == "3-5-1-1 x1, 4-4-1-1 x1"


# --- 4. xA vs xAG ------------------------------------------------------------------------------


def test_xa_xag_needs_aggregates(synced_season: Engine) -> None:
    with Session(synced_season) as session, pytest.raises(CheckError, match="aggregate"):
        check_xa_xag(session, season_id(session))


def test_xa_xag_per_match_and_per_group(synced_season: Engine) -> None:
    # The fixture has no xag key (absence 0) and xa 0.35 for the AM only. Give B's AM
    # (2 matches) a bigger xag than A's AM (1 match) has, against the xa order: tied on xa,
    # B ranks above A on xag.
    set_value(synced_season, "p_BAM01", "m_FIXT01", "xag", 0.5)
    set_value(synced_season, "p_BAM01", "m_FIXT02", "xag", 0.5)
    aggregate(synced_season)
    with Session(synced_season) as session:
        check = check_xa_xag(session, season_id(session))
    # 4 outfield rows per match have both; equal unless one of them is the AM.
    assert (check.with_both, check.xa_only, check.xag_only) == (12, 0, 0)
    assert check.equal == 9  # the 3 AM rows differ (0.35 vs 0.5 or 0)
    assert check.total_xa == pytest.approx(3 * 0.35)
    assert check.total_xag == pytest.approx(1.0)
    am = next(g for g in check.groups if g.peer_role == "W")  # the fixture's 84 is W (D-016)
    assert am.ranked_on_both == 2
    assert (am.mean_abs_gap, am.max_abs_gap) == (25, 25)  # 50/50 on xa; 75/25 on xag
    assert {(m.team, m.gap) for m in am.movers} == {
        ("Example Athletic", -25),
        ("Sample Rovers", 25),
    }
    # Groups where nothing moves list no movers; keepers have neither metric: no GK group.
    assert all(not g.movers for g in check.groups if g.peer_role != "W")
    assert "GK" not in {g.peer_role for g in check.groups}


# --- the script ---------------------------------------------------------------------------------


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_2c", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_prints_every_check(
    synced_season: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    aggregate(synced_season)
    script = load_script()
    with Session(synced_season) as session:
        sid = season_id(session)
        script.print_fouled(check_was_fouled(session, sid))
        for spec in RATE_SPECS:
            script.print_rate(check_rate(session, sid, spec))
        for slot_spec in SLOT_SPECS:
            script.print_slots(check_slots(session, sid, slot_spec), 1)
        script.print_xa(check_xa_xag(session, sid))
        script.print_xa_definition(check_xa_definition(session, sid))
    out = capsys.readouterr().out
    assert "stored was_fouled values (outfield rows): NULL: 3, 1: 3, 2: 3, 3: 3" in out
    assert "<=-3: 6" in out
    assert "launch_pct vs launches / distributions · PitchAPI sends a percent (0-100)" in out
    assert "3-4-2-1 slots 84/86 (mapped W): 3 starts" in out
    assert "... and 1 more (1 starts, 90 min)" in out
    assert "4-2-3-1 slots 64/66 (mapped DM): 0 starts" in out and "nobody started" in out
    assert "per match: 12 rows with both" in out
    assert "chances_created = 0: 9 rows · xa > 0 in 3 (33.3%)" in out


# --- 5. xA definition -------------------------------------------------------------------------


def test_xa_definition_counts_xa_without_chances(synced_season: Engine) -> None:
    # Fixture: the AM has xa 0.35 and 2 advanced key passes, but no /players chances_created
    # key (so 0); the left-back has 1 of each; striker and sub have none. xag is 0 throughout.
    with Session(synced_season) as session:
        check = check_xa_definition(session, season_id(session))
    assert check.rows == 12 and check.total_xa == pytest.approx(3 * 0.35)
    by_count = {row.count_code: row for row in check.no_chance}
    players = by_count["chances_created"]  # 0 for everyone but the left-back
    assert (players.rows, players.xa_positive, players.xag_positive) == (9, 3, 0)
    assert players.xa_share == pytest.approx(1.0)
    advanced = by_count["key_passes"]  # 0 for the striker and the sub
    assert (advanced.rows, advanced.xa_positive, advanced.xag_positive) == (6, 0, 0)
    assert advanced.xa_share == pytest.approx(0.0)


def test_xa_definition_sees_xag_without_chances(synced_season: Engine) -> None:
    set_value(synced_season, "p_AST01", "m_FIXT01", "xag", 0.2)  # xag but no key pass
    with Session(synced_season) as session:
        check = check_xa_definition(session, season_id(session))
    assert {r.count_code: r.xag_positive for r in check.no_chance} == {
        "chances_created": 1, "key_passes": 1,
    }  # fmt: skip
