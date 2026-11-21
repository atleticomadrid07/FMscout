"""Peer groups and percentiles (slice 2b, D-014).

The formula is tested directly; the rest runs on the three-match fixture season, where the
minimum minutes is 45 (2 matches x 90 x 25%) and the groups are GK 4, FB 2, W 2, ST 2.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session

from scouting.db.models import (
    Appearance,
    MatchSourceId,
    Metric,
    PeerGroup,
    Percentile,
    PlayerSourceId,
    StatValue,
)
from scouting.ingest.season import find_season
from scouting.metrics.aggregate import AggregateSummary, aggregate_season
from scouting.metrics.percentiles import midpoint_percentile, rank_against

LEAGUE, SEASON = "l_FIXT01", "2025/2026"
Snapshot = Callable[[Engine], dict[str, list[tuple[Any, ...]]]]  # the conftest fixture


# --- the formula ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "population", "expected"),
    [
        (3, [1, 2, 3, 4], 62.5),  # 2 below, itself counts half: 2.5 / 4
        (1, [1, 2, 3, 4], 12.5),  # the lowest isn't 0: it still counts half of itself
        (4, [1, 2, 3, 4], 87.5),  # nor is the highest 100
        (0, [0, 0, 0, 1], 37.5),  # three tied zeros: the middle of their block
        (1, [0, 0, 0, 1], 87.5),
        (5, [5, 5, 5], 50.0),  # everyone tied: everyone at 50
        (7, [7], 50.0),  # a group of one
        (10, [1, 2], 100.0),  # a value from outside the group (added by hand)
        (-1, [1, 2], 0.0),
        (1.5, [1, 2], 50.0),
    ],
)
def test_midpoint_percentile(value: float, population: list[float], expected: float) -> None:
    assert midpoint_percentile(value, population) == pytest.approx(expected)


def test_the_design_example() -> None:
    # D-014: 60 of 100 CBs won no penalties; they sit at the 30th, not the 0th or 60th.
    population = sorted([0.0] * 60 + [0.1 + i / 100 for i in range(40)])
    assert midpoint_percentile(0.0, population) == pytest.approx(30.0)


def test_empty_population_is_refused() -> None:
    with pytest.raises(ValueError, match="empty"):
        midpoint_percentile(1.0, [])


# --- on the fixture season ------------------------------------------------------------------


def aggregate(engine: Engine) -> AggregateSummary:
    with Session(engine) as session, session.begin():
        return aggregate_season(session, find_season(session, LEAGUE, SEASON).id)


def ranks(engine: Engine, code: str) -> dict[str, tuple[float, float, int]]:
    """player external ID → (value, pct, ranked among) for one metric."""
    with Session(engine) as session:
        return {
            ext: (value, pct, n)
            for ext, value, pct, n in session.execute(
                select(
                    PlayerSourceId.external_id,
                    Percentile.value,
                    Percentile.pct,
                    Percentile.ranked_among,
                )
                .join(PlayerSourceId, PlayerSourceId.player_id == Percentile.player_id)
                .join(Metric, Metric.id == Percentile.metric_id)
                .where(Metric.code == code)
            )
        }


def appearance_id(session: Session, player: str, match: str) -> int:
    return session.scalars(
        select(Appearance.id)
        .join(PlayerSourceId, PlayerSourceId.player_id == Appearance.player_id)
        .join(MatchSourceId, MatchSourceId.match_id == Appearance.match_id)
        .where(PlayerSourceId.external_id == player, MatchSourceId.external_id == match)
    ).one()


def metric_id(session: Session, code: str) -> int:
    return session.scalars(select(Metric.id).where(Metric.code == code)).one()


def test_groups_hold_role_players_with_enough_minutes(synced_season: Engine) -> None:
    summary = aggregate(synced_season)
    assert summary.percentiles is not None
    assert summary.percentiles.group_sizes == {"GK": 4, "FB": 2, "W": 2, "ST": 2}
    assert sorted(summary.percentiles.small_groups) == ["FB", "GK", "ST", "W"]
    # The sub keeper (10 min) has a role but too few minutes: he ranks nowhere.
    assert "p_BGK03" not in ranks(synced_season, "saves")


def test_per90_percentiles_and_ties(synced_season: Engine) -> None:
    aggregate(synced_season)
    goals = ranks(synced_season, "goals")
    # Both strikers: 1 goal in 78 min per match, so the same per 90: tied at 50.
    assert goals["p_AST01"][1:] == goals["p_CST01"][1:] == (50.0, 2)
    saves = ranks(synced_season, "saves")  # per 90: C's keeper 3, A's first 3, B's and A's second 1
    assert saves["p_CGK01"][1] == saves["p_AGK01"][1] == 75.0
    assert saves["p_BGK02"][1] == saves["p_AGK02"][1] == 25.0


def test_percentiles_are_stored_raw_for_lower_is_better(synced_season: Engine) -> None:
    aggregate(synced_season)
    conceded = ranks(synced_season, "goals_conceded")  # lower is better: never flipped here
    highest = max(conceded.values(), key=lambda r: r[0])
    lowest = min(conceded.values(), key=lambda r: r[0])
    assert highest[1] > lowest[1]


def test_rates_need_the_minimum_attempts(synced_season: Engine) -> None:
    aggregate(synced_season)
    assert ranks(synced_season, "distribution_accuracy") == {}  # 25-60 < 100 distributions
    with Session(synced_season) as session, session.begin():
        session.execute(
            update(Metric).where(Metric.code == "distribution_accuracy").values(min_attempts=40)
        )
    aggregate(synced_season)
    accuracy = ranks(synced_season, "distribution_accuracy")
    assert set(accuracy) == {"p_CGK01", "p_BGK02"}  # 60 and 50 distributions
    value, pct, ranked_among = accuracy["p_CGK01"]
    assert (round(value, 6), pct, ranked_among) == (80.0, 75.0, 2)  # 80% beats 60%


def test_per90_needs_the_minimum_minutes_on_that_metric(synced_season: Engine) -> None:
    # The striker plays 78 + 10 min, but has advanced data only for the 10-minute match.
    with Session(synced_season) as session, session.begin():
        short = appearance_id(session, "p_CST01", "m_FIXT03")
        session.execute(update(Appearance).where(Appearance.id == short).values(minutes=10))
        advanced = select(Metric.id).where(Metric.source == "pitchapi_advanced")
        session.execute(
            delete(StatValue).where(
                StatValue.appearance_id == appearance_id(session, "p_CST01", "m_FIXT02"),
                StatValue.metric_id.in_(advanced),
            )
        )
    aggregate(synced_season)
    assert "p_CST01" in ranks(synced_season, "goals")  # 88 min of /players data: ranked
    assert "p_CST01" not in ranks(synced_season, "xt")  # 10 min of advanced data: not
    assert ranks(synced_season, "xt")["p_AST01"][2] == 1  # ranked among 1


def test_unknown_values_are_not_ranked(synced_season: Engine) -> None:
    # Unknown touches ("skip") in every match of both full-backs: no per 90, no percentile.
    with Session(synced_season) as session, session.begin():
        appearances = [
            appearance_id(session, player, match)
            for player, match in (
                ("p_ALB01", "m_FIXT01"), ("p_CLB01", "m_FIXT02"), ("p_CLB01", "m_FIXT03"),
            )
        ]  # fmt: skip
        session.execute(
            update(StatValue)
            .where(
                StatValue.appearance_id.in_(appearances),
                StatValue.metric_id == metric_id(session, "touches"),
            )
            .values(value=None)
        )
    aggregate(synced_season)
    assert ranks(synced_season, "touches").keys().isdisjoint({"p_ALB01", "p_CLB01"})
    # was_fouled NULL counts as 0 (D-016), so both are ranked on it: the FB group is just
    # these two, tied at 0, so both sit at 50.
    fouled = ranks(synced_season, "was_fouled")
    assert fouled["p_ALB01"][:2] == fouled["p_CLB01"][:2] == (0.0, 50.0)


def test_rebuild_is_identical_and_keeps_group_ids(
    synced_season: Engine, snapshot: Snapshot
) -> None:
    aggregate(synced_season)
    with Session(synced_season) as session:
        ids = sorted(session.scalars(select(PeerGroup.id)))
    first = snapshot(synced_season)
    aggregate(synced_season)
    assert snapshot(synced_season) == first
    with Session(synced_season) as session:
        assert sorted(session.scalars(select(PeerGroup.id))) == ids


def test_a_role_with_no_qualifying_players_loses_its_group(synced_season: Engine) -> None:
    aggregate(synced_season)
    with Session(synced_season) as session, session.begin():
        strikers = select(PlayerSourceId.player_id).where(
            PlayerSourceId.external_id.in_(["p_AST01", "p_CST01"])
        )
        session.execute(
            update(Appearance).where(Appearance.player_id.in_(strikers)).values(minutes=5)
        )
    summary = aggregate(synced_season)
    assert summary.percentiles is not None and "ST" not in summary.percentiles.group_sizes
    with Session(synced_season) as session:
        assert session.scalars(select(PeerGroup).where(PeerGroup.peer_role == "ST")).all() == []
    assert ranks(synced_season, "goals").keys().isdisjoint({"p_AST01", "p_CST01"})


def test_rank_against_places_a_value_without_joining(synced_season: Engine) -> None:
    aggregate(synced_season)
    with Session(synced_season) as session:
        group = session.scalars(select(PeerGroup).where(PeerGroup.peer_role == "ST")).one()
        goals = metric_id(session, "goals")
        assert rank_against(session, group.id, goals, 5.0) == (100.0, 2)  # above both
        assert rank_against(session, group.id, goals, 0.0) == (0.0, 2)  # below both
        saves = metric_id(session, "saves")
        assert rank_against(session, group.id, saves, 1.0) is None  # nobody ranks on it
        assert (
            session.scalar(
                select(Percentile.ranked_among)
                .where(Percentile.peer_group_id == group.id, Percentile.metric_id == goals)
                .limit(1)
            )
            == 2
        )  # the group itself didn't change
