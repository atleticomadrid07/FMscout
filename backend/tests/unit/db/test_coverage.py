"""metric_coverage (slice 2e): per metric and peer group, from the stored percentiles."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from scouting.db.models import Metric, PeerGroup, Percentile, Scope, Season
from scouting.db.queries import _quantile, metric_coverage
from scouting.metrics.aggregate import aggregate_season


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [([5.0], 0.9, 5.0), ([0.0, 10.0], 0.5, 5.0), ([1.0, 2.0, 3.0, 4.0], 0.9, 3.7)],
)
def test_quantile(values: list[float], q: float, expected: float) -> None:
    assert _quantile(values, q) == pytest.approx(expected)


def test_coverage_matches_the_stored_percentiles(synced_season: Engine) -> None:
    with Session(synced_season) as session:
        season_id = session.scalar(select(Season.id))
        assert season_id is not None
        aggregate_season(session, season_id)
        session.commit()
        scope_id = session.scalar(select(Scope.id))
        assert scope_id is not None
        groups, rows = metric_coverage(session, scope_id)
        assert [g.peer_role for g in groups] == ["GK", "FB", "W", "ST"]
        assert len(rows) == session.scalar(select(func.count()).select_from(Metric))
        by_code = {r.code: r for r in rows}

        saves = by_code["saves"]
        assert saves.cells["ST"].ranked == 0 and saves.cells["ST"].median is None
        assert saves.cells["GK"].ranked == session.scalar(
            select(func.count())
            .select_from(Percentile)
            .join(PeerGroup, PeerGroup.id == Percentile.peer_group_id)
            .join(Metric, Metric.id == Percentile.metric_id)
            .where(PeerGroup.peer_role == "GK", Metric.code == "saves")
        )
        goals = by_code["goals"].cells["ST"]
        stored = sorted(
            session.scalars(
                select(Percentile.value)
                .join(PeerGroup, PeerGroup.id == Percentile.peer_group_id)
                .join(Metric, Metric.id == Percentile.metric_id)
                .where(PeerGroup.peer_role == "ST", Metric.code == "goals")
            )
        )
    assert goals.ranked == len(stored) == 2
    assert goals.median == pytest.approx(sum(stored) / 2)
    assert goals.zero_share == sum(v == 0 for v in stored) / 2
    assert by_code["goals"].basis == "per90"
    assert by_code["passes_completed"].basis == "rate"
