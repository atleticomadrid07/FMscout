"""Season aggregates (slice 2a) on the three-match fixture season from conftest.py.

Team C plays the same template in both its matches (m_FIXT02, m_FIXT03), so a C player's
season is exactly twice one match: easy to check by hand. Some tests edit stored values
first to create the case they need (a NULL, a different per-match rate, a second role).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session

from scouting.db.models import (
    Appearance,
    Match,
    MatchSourceId,
    Metric,
    PlayerScope,
    PlayerScopeRole,
    PlayerScopeStat,
    PlayerScopeTeam,
    PlayerSourceId,
    Scope,
    StatValue,
    Team,
    TeamSourceId,
)
from scouting.ingest.season import find_season
from scouting.metrics.aggregate import (
    AggregateError,
    AggregateSummary,
    aggregate_season,
    default_min_minutes,
)

LEAGUE, SEASON = "l_FIXT01", "2025/2026"
Snapshot = Callable[[Engine], dict[str, list[tuple[Any, ...]]]]  # the conftest fixture


def aggregate(engine: Engine) -> AggregateSummary:
    with Session(engine) as session, session.begin():
        return aggregate_season(session, find_season(session, LEAGUE, SEASON).id)


def player_id(session: Session, external_id: str) -> int:
    return session.scalars(
        select(PlayerSourceId.player_id).where(PlayerSourceId.external_id == external_id)
    ).one()


def appearance_id(session: Session, player: str, match: str) -> int:
    return session.scalars(
        select(Appearance.id)
        .join(MatchSourceId, MatchSourceId.match_id == Appearance.match_id)
        .where(Appearance.player_id == player_id(session, player))
        .where(MatchSourceId.external_id == match)
    ).one()


def metric_id(session: Session, code: str) -> int:
    return session.scalars(select(Metric.id).where(Metric.code == code)).one()


def stat(engine: Engine, player: str, code: str) -> PlayerScopeStat | None:
    with Session(engine) as session:
        return session.scalars(
            select(PlayerScopeStat).where(
                PlayerScopeStat.player_id == player_id(session, player),
                PlayerScopeStat.metric_id == metric_id(session, code),
            )
        ).one_or_none()


def player_scope(engine: Engine, player: str) -> PlayerScope:
    with Session(engine) as session:
        return session.scalars(
            select(PlayerScope).where(PlayerScope.player_id == player_id(session, player))
        ).one()


def set_value(
    engine: Engine, player: str, match: str, code: str, value: float | None,
    denominator: float | None = None,
) -> None:  # fmt: skip
    with Session(engine) as session, session.begin():
        session.execute(
            update(StatValue)
            .where(
                StatValue.appearance_id == appearance_id(session, player, match),
                StatValue.metric_id == metric_id(session, code),
            )
            .values(value=value, denominator=denominator)
        )


# --- totals, per 90, rates ------------------------------------------------------------------


def test_counts_sum_and_per90_uses_the_metric_minutes(synced_season: Engine) -> None:
    aggregate(synced_season)
    goals = stat(synced_season, "p_CST01", "goals")  # 1 goal in 78 min, twice
    assert goals is not None
    assert (goals.total, goals.minutes, goals.matches_with_value) == (2, 156, 2)
    assert goals.per90 == pytest.approx(2 / 156 * 90)  # 1.1538
    assert goals.rate is None and goals.denominator is None


def test_fractions_rate_from_season_sums(synced_season: Engine) -> None:
    aggregate(synced_season)
    take_ons = stat(synced_season, "p_BAM01", "take_ons_completed")  # 3 of 5, twice
    assert take_ons is not None
    assert (take_ons.total, take_ons.denominator, take_ons.rate) == (6, 10, 0.6)


def test_rates_never_average_per_match_percentages(synced_season: Engine) -> None:
    # C's keeper: 80% of 30 distributions in m_FIXT02; make m_FIXT03 50% of 10.
    set_value(synced_season, "p_CGK01", "m_FIXT03", "distribution_accuracy", 50 * 10, 10)
    aggregate(synced_season)
    accuracy = stat(synced_season, "p_CGK01", "distribution_accuracy")
    assert accuracy is not None
    # Weighted: (80 x 30 + 50 x 10) / 40 = 72.5. The plain average would say 65.
    assert accuracy.rate == pytest.approx(72.5)
    assert accuracy.per90 is None


def test_no_attempts_gives_a_null_rate(synced_season: Engine) -> None:
    aggregate(synced_season)
    take_ons = stat(synced_season, "p_CLB01", "take_ons_completed")  # 0 of 0, twice
    assert take_ons is not None
    assert (take_ons.total, take_ons.denominator, take_ons.rate) == (0, 0, None)
    assert take_ons.minutes == 180  # zero attempts still counts as exposure


# --- NULL handling and exposure -------------------------------------------------------------


def test_unknown_null_adds_neither_total_nor_minutes(synced_season: Engine) -> None:
    # A provider null on a "skip" metric (the default), in both of C's left-back's matches.
    set_value(synced_season, "p_CLB01", "m_FIXT02", "touches", None)
    set_value(synced_season, "p_CLB01", "m_FIXT03", "touches", None)
    aggregate(synced_season)
    touches = stat(synced_season, "p_CLB01", "touches")
    assert touches is not None
    assert (touches.total, touches.minutes, touches.matches_with_value, touches.per90) == (
        None, 0, 0, None,
    )  # fmt: skip


def test_mixed_null_counts_only_the_known_match(synced_season: Engine) -> None:
    set_value(synced_season, "p_CLB01", "m_FIXT02", "touches", None)
    set_value(synced_season, "p_CLB01", "m_FIXT03", "touches", 40)
    aggregate(synced_season)
    touches = stat(synced_season, "p_CLB01", "touches")
    assert touches is not None
    assert (touches.total, touches.minutes, touches.matches_with_value) == (40, 90, 1)
    assert touches.per90 == pytest.approx(40.0)  # per 90 of the 90 known minutes, not 180


def test_was_fouled_null_counts_as_zero(synced_season: Engine) -> None:
    # C's left-back: was_fouled NULL in both matches. PitchAPI never sends 0 (2c, D-016).
    aggregate(synced_season)
    fouled = stat(synced_season, "p_CLB01", "was_fouled")
    assert fouled is not None
    assert (fouled.total, fouled.minutes, fouled.matches_with_value, fouled.per90) == (
        0, 180, 2, 0,
    )  # fmt: skip


def test_xgot_null_counts_as_zero(synced_season: Engine) -> None:
    # B's 10 never shot on target: xGOT NULL in both matches (season_null = "zero").
    aggregate(synced_season)
    xgot = stat(synced_season, "p_BAM01", "xgot")
    assert xgot is not None
    assert (xgot.total, xgot.minutes, xgot.matches_with_value, xgot.per90) == (0, 180, 2, 0)


def test_missing_rows_add_no_exposure(synced_season: Engine) -> None:
    # As if m_FIXT03 had no advanced analytics: drop its advanced rows.
    with Session(synced_season) as session, session.begin():
        advanced = select(Metric.id).where(Metric.source == "pitchapi_advanced")
        in_match = (
            select(Appearance.id)
            .join(MatchSourceId, MatchSourceId.match_id == Appearance.match_id)
            .where(MatchSourceId.external_id == "m_FIXT03")
        )
        session.execute(
            delete(StatValue).where(
                StatValue.metric_id.in_(advanced), StatValue.appearance_id.in_(in_match)
            )
        )
    aggregate(synced_season)
    xt, goals = stat(synced_season, "p_CST01", "xt"), stat(synced_season, "p_CST01", "goals")
    assert xt is not None and goals is not None
    assert (xt.minutes, xt.matches_with_value) == (78, 1)  # only m_FIXT02
    assert xt.per90 == pytest.approx(0.21 / 78 * 90)
    assert goals.minutes == 156  # /players still covered both matches


def test_metrics_that_dont_apply_get_no_season_row(synced_season: Engine) -> None:
    aggregate(synced_season)
    assert stat(synced_season, "p_CST01", "saves") is None
    assert stat(synced_season, "p_CGK01", "goals") is None
    assert stat(synced_season, "p_BGK03", "distribution_accuracy") is None  # sub keeper


# --- players: minutes, roles, rating --------------------------------------------------------


def test_player_minutes_starts_and_rating(synced_season: Engine) -> None:
    aggregate(synced_season)
    striker = player_scope(synced_season, "p_CST01")
    assert (striker.minutes, striker.matches, striker.starts) == (156, 2, 2)
    assert striker.avg_rating == pytest.approx(7.9)
    assert striker.rated_matches == 2
    sub_keeper = player_scope(synced_season, "p_BGK03")  # 5 minutes, twice: never rated
    assert (sub_keeper.starts, sub_keeper.avg_rating, sub_keeper.rated_matches) == (0, None, 0)


def test_primary_role_from_starts(synced_season: Engine) -> None:
    aggregate(synced_season)
    fullback = player_scope(synced_season, "p_CLB01")
    assert (fullback.primary_role, fullback.peer_role, fullback.role_source) == (
        "FB", "FB", "starts",
    )  # fmt: skip


def test_most_starter_minutes_wins_and_ties_follow_role_order(synced_season: Engine) -> None:
    with Session(synced_season) as session, session.begin():
        winger = appearance_id(session, "p_CLB01", "m_FIXT03")
        session.execute(update(Appearance).where(Appearance.id == winger).values(role="W"))
    aggregate(synced_season)
    assert player_scope(synced_season, "p_CLB01").primary_role == "FB"  # 90 v 90: FB first

    with Session(synced_season) as session, session.begin():
        fullback = appearance_id(session, "p_CLB01", "m_FIXT02")
        session.execute(update(Appearance).where(Appearance.id == fullback).values(minutes=60))
    aggregate(synced_season)
    assert player_scope(synced_season, "p_CLB01").primary_role == "W"  # 90 v 60
    with Session(synced_season) as session:
        roles = session.execute(
            select(PlayerScopeRole.role, PlayerScopeRole.minutes, PlayerScopeRole.starts)
            .where(PlayerScopeRole.player_id == player_id(session, "p_CLB01"))
            .order_by(PlayerScopeRole.role)
        ).all()
    assert [tuple(r) for r in roles] == [("FB", 60, 1), ("W", 90, 1)]


def test_no_starts_falls_back_to_the_listed_slot(synced_season: Engine) -> None:
    aggregate(synced_season)
    keeper = player_scope(synced_season, "p_BGK03")  # sub keeper, profile slot 11
    assert (keeper.primary_role, keeper.peer_role, keeper.role_source) == ("GK", "GK", "listed")
    sub = player_scope(synced_season, "p_CSB01")  # profile gives only broad category 2
    assert (sub.primary_role, sub.peer_role, sub.role_source) == (None, None, None)


def test_minutes_per_team_after_a_move(synced_season: Engine) -> None:
    with Session(synced_season) as session, session.begin():
        moved = appearance_id(session, "p_CST01", "m_FIXT03")
        team_a = session.scalars(
            select(TeamSourceId.team_id).where(TeamSourceId.external_id == "t_FIXTA1")
        ).one()
        session.execute(update(Appearance).where(Appearance.id == moved).values(team_id=team_a))
    aggregate(synced_season)
    with Session(synced_season) as session:
        rows = session.execute(
            select(Team.name, PlayerScopeTeam.minutes, PlayerScopeTeam.matches)
            .join(Team, Team.id == PlayerScopeTeam.team_id)
            .where(PlayerScopeTeam.player_id == player_id(session, "p_CST01"))
            .order_by(Team.name)
        ).all()
    assert len(rows) == 2 and [(m, n) for _, m, n in rows] == [(78, 1), (78, 1)]


# --- the scope as a whole -----------------------------------------------------------------------


def test_summary_and_min_minutes(synced_season: Engine) -> None:
    summary = aggregate(synced_season)
    assert summary.players == 14
    assert summary.min_minutes == 45  # 2 matches per team x 90 x 25%
    # The starters (78-180 min) qualify; the subs (5-24 min) don't.
    assert summary.peer_roles == {"GK": 4, "FB": 2, "W": 2, "ST": 2}
    assert summary.role_sources == {"starts": 10, "listed": 2, None: 2}
    with Session(synced_season) as session:
        assert default_min_minutes(session, find_season(session, LEAGUE, SEASON).id) == 45


def test_rebuild_is_identical_and_keeps_the_scope_id(
    synced_season: Engine, snapshot: Snapshot
) -> None:
    aggregate(synced_season)
    first = snapshot(synced_season)
    aggregate(synced_season)
    assert snapshot(synced_season) == first
    with Session(synced_season) as session:
        assert len(session.scalars(select(Scope)).all()) == 1


def test_rebuild_drops_rows_that_no_longer_exist(synced_season: Engine) -> None:
    aggregate(synced_season)
    with Session(synced_season) as session, session.begin():
        session.execute(delete(StatValue).where(StatValue.metric_id == metric_id(session, "xt")))
    aggregate(synced_season)
    assert stat(synced_season, "p_CST01", "xt") is None


def test_season_without_synced_matches_is_refused(synced_season: Engine) -> None:
    with Session(synced_season) as session, session.begin():
        session.execute(update(Match).values(normalized_at=None))
    with pytest.raises(AggregateError, match="no synced matches"):
        aggregate(synced_season)
