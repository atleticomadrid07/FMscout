"""Slice 3d: percentiles computed on the fly (metrics.percentiles.live_group) must give the
stored percentiles exactly at the stored threshold, on the three-match fixture season."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from scouting.db.models import Metric, PeerGroup, Percentile, PlayerScope, Scope
from scouting.ingest.season import find_season
from scouting.metrics.aggregate import aggregate_season
from scouting.metrics.percentiles import eligible_value, live_group, midpoint_percentile
from scouting.metrics.registry import BY_CODE

LEAGUE, SEASON = "l_FIXT01", "2025/2026"


def scope_id(session: Session) -> int:
    season = find_season(session, LEAGUE, SEASON)
    return session.scalars(select(Scope.id).where(Scope.season_id == season.id)).one()


def test_live_groups_reproduce_every_stored_percentile(synced_season: Engine) -> None:
    with Session(synced_season) as session, session.begin():
        aggregate_season(session, find_season(session, LEAGUE, SEASON).id)
    with Session(synced_season) as session:
        sid = scope_id(session)
        codes = list(BY_CODE)
        code_of: dict[int, str] = {
            mid: code for mid, code in session.execute(select(Metric.id, Metric.code))
        }
        checked = 0
        for group in session.scalars(select(PeerGroup).where(PeerGroup.scope_id == sid)):
            live = live_group(session, sid, group.peer_role, group.min_minutes, codes)
            assert live.size == group.size
            stored = session.scalars(
                select(Percentile).where(Percentile.peer_group_id == group.id)
            ).all()
            per_metric: dict[str, int] = {}
            for row in stored:
                code = code_of[row.metric_id]
                assert row.player_id in live.members
                assert live.rank(code, row.value) == (row.pct, row.ranked_among)
                per_metric[code] = per_metric.get(code, 0) + 1
                checked += 1
            # Same eligible players per metric: nothing ranked live that isn't stored.
            assert {c: len(v) for c, v in live.populations.items() if v} == per_metric
        assert checked > 0


def test_a_higher_threshold_shrinks_the_group(synced_season: Engine) -> None:
    with Session(synced_season) as session, session.begin():
        aggregate_season(session, find_season(session, LEAGUE, SEASON).id)
    with Session(synced_season) as session:
        sid = scope_id(session)
        live = live_group(session, sid, "GK", 100, ["saves"])
        keepers = session.scalars(
            select(PlayerScope.minutes).where(
                PlayerScope.scope_id == sid, PlayerScope.peer_role == "GK"
            )
        ).all()
        assert live.size == sum(1 for m in keepers if m >= 100) == 2
        assert len(live.populations["saves"]) == 2
        assert live_group(session, sid, "GK", 10_000, ["saves"]).size == 0
        assert live_group(session, sid, "GK", 10_000, ["saves"]).rank("saves", 1.0) is None


def test_outsider_rank_uses_the_members_only() -> None:
    population = [1.0, 2.0, 3.0]
    # A member's own value is in the population; an outsider's isn't added to it.
    assert midpoint_percentile(2.0, population) == 50.0
    assert midpoint_percentile(2.5, population) == pytest.approx(200 / 3)


def test_eligible_value() -> None:
    def per90(minutes: int) -> float | None:
        return eligible_value(
            is_fraction=False, rate=None, per90=1.5, denominator=None, minutes=minutes,
            min_attempts=None, min_minutes=900,
        )  # fmt: skip

    assert per90(899) is None and per90(900) == 1.5

    def rate(attempts: float | None, rate_value: float | None = 0.5) -> float | None:
        return eligible_value(
            is_fraction=True, rate=rate_value, per90=None, denominator=attempts, minutes=0,
            min_attempts=20, min_minutes=900,
        )  # fmt: skip

    assert rate(19) is None and rate(20) == 0.5 and rate(None) is None
    assert rate(30, None) is None  # no attempts made it undefined
