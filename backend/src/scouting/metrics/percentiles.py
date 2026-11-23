"""Peer groups and percentiles (Phase 2, slice 2b; D-014).

For each peer role in a scope, the group is every player with that role and at least the
default minimum minutes. Each metric then ranks the group's *eligible* players:
- per-90 metrics: at least the minimum minutes on that metric's own exposure;
- rate metrics: at least the metric's `min_attempts` (season attempts, or weight);
- a metric with no season row for a player (it doesn't apply) doesn't rank him.

Percentile = midpoint: (players below + half the players tied) / n x 100. Stored raw (low
value = low percentile); the UI flips lower-is-better metrics using `metric.direction`.

Runs inside `aggregate_season`, in the same transaction, after the season stats.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from scouting.db.models import Metric, PeerGroup, Percentile, PlayerScope, PlayerScopeStat
from scouting.domain.positions import PEER_ROLES

SMALL_GROUP: Final = 20  # warn below this many players (D-014)


def midpoint_percentile(value: float, population: Sequence[float]) -> float:
    """Percentile of `value` within `population` (sorted ascending), 0-100.

    Players tied with `value` count half: a block of tied zeros sits in the middle of the
    block rather than at its bottom (percent_rank) or top (cumulative). `value` need not be
    in `population`: that's how a player added by hand is ranked without joining the group.
    """
    if not population:
        raise ValueError("can't rank against an empty population")
    below = bisect_left(population, value)
    tied = bisect_right(population, value) - below
    return (below + tied / 2) / len(population) * 100


@dataclass(slots=True)
class PercentileSummary:
    min_minutes: int
    group_sizes: dict[str, int] = field(default_factory=dict)
    rows: int = 0

    @property
    def small_groups(self) -> list[str]:
        return [role for role, size in self.group_sizes.items() if size < SMALL_GROUP]


@dataclass(frozen=True, slots=True)
class _Eligible:
    player_id: int
    value: float


def eligible_value(
    *,
    is_fraction: bool,
    rate: float | None,
    per90: float | None,
    denominator: float | None,
    minutes: int,
    min_attempts: int | None,
    min_minutes: int,
) -> float | None:
    """The value a player is ranked on for a metric, or None if he isn't eligible: a rate
    needs the metric's minimum attempts, a per-90 the minimum minutes on its own exposure."""
    if is_fraction:
        if rate is None or denominator is None:
            return None
        if min_attempts is not None and denominator < min_attempts:
            return None
        return rate
    if per90 is None or minutes < min_minutes:
        return None
    return per90


def _eligible_value(stat: PlayerScopeStat, metric: Metric, min_minutes: int) -> float | None:
    return eligible_value(
        is_fraction=metric.aggregation == "rate_from_sums",
        rate=stat.rate,
        per90=stat.per90,
        denominator=stat.denominator,
        minutes=stat.minutes,
        min_attempts=metric.min_attempts,
        min_minutes=min_minutes,
    )


def basis_for(metric: Metric) -> str:
    return "rate" if metric.aggregation == "rate_from_sums" else "per90"


def build_percentiles(
    session: Session, scope_id: int, min_minutes: int, computed_at: datetime
) -> PercentileSummary:
    """Rebuild every peer group and percentile of one scope. Call inside a transaction."""
    summary = PercentileSummary(min_minutes=min_minutes)
    existing = {
        (g.peer_role, g.min_minutes): g
        for g in session.scalars(select(PeerGroup).where(PeerGroup.scope_id == scope_id))
    }
    if existing:
        session.execute(
            delete(Percentile).where(
                Percentile.peer_group_id.in_([g.id for g in existing.values()])
            )
        )

    members: dict[str, list[int]] = defaultdict(list)
    for player_id, peer_role in session.execute(
        select(PlayerScope.player_id, PlayerScope.peer_role).where(
            PlayerScope.scope_id == scope_id,
            PlayerScope.peer_role.is_not(None),
            PlayerScope.minutes >= min_minutes,
        )
    ):
        if peer_role is not None:  # filtered above; narrows the type
            members[peer_role].append(player_id)

    # Upsert one group per role that has members; drop groups that no longer do.
    groups: dict[str, PeerGroup] = {}
    for role in PEER_ROLES:
        if not members.get(role):
            continue  # an existing group for this role stays in `existing` and is deleted
        group = existing.pop((role, min_minutes), None)
        if group is None:
            group = PeerGroup(scope_id=scope_id, peer_role=role, min_minutes=min_minutes)
            session.add(group)
        group.size = len(members[role])
        group.computed_at = computed_at
        groups[role] = group
        summary.group_sizes[role] = group.size
    for stale in existing.values():
        session.delete(stale)
    session.flush()

    metrics = {m.id: m for m in session.scalars(select(Metric))}
    role_of = {pid: role for role, pids in members.items() for pid in pids}
    eligible: dict[tuple[str, int], list[_Eligible]] = defaultdict(list)
    for stat in session.scalars(
        select(PlayerScopeStat).where(
            PlayerScopeStat.scope_id == scope_id,
            PlayerScopeStat.player_id.in_(list(role_of)),
        )
    ):
        value = _eligible_value(stat, metrics[stat.metric_id], min_minutes)
        if value is not None:
            eligible[(role_of[stat.player_id], stat.metric_id)].append(
                _Eligible(stat.player_id, value)
            )

    rows: list[dict[str, object]] = []
    for (group_role, metric_id), players in sorted(eligible.items()):
        population = sorted(p.value for p in players)
        basis = basis_for(metrics[metric_id])
        for player in sorted(players, key=lambda p: p.player_id):
            rows.append(
                {
                    "peer_group_id": groups[group_role].id,
                    "player_id": player.player_id,
                    "metric_id": metric_id,
                    "basis": basis,
                    "value": player.value,
                    "pct": midpoint_percentile(player.value, population),
                    "ranked_among": len(population),
                }
            )
    if rows:
        session.execute(insert(Percentile), rows)
    summary.rows = len(rows)
    return summary


def rank_against(
    session: Session, peer_group_id: int, metric_id: int, value: float
) -> tuple[float, int] | None:
    """Rank any value against a group's eligible players without joining the group: for a
    player added to a comparison by hand (below the minutes or attempts, or another role).
    Returns (percentile, ranked among), or None if nobody in the group ranks on the metric."""
    population = sorted(
        session.scalars(
            select(Percentile.value).where(
                Percentile.peer_group_id == peer_group_id, Percentile.metric_id == metric_id
            )
        )
    )
    if not population:
        return None
    return midpoint_percentile(value, population), len(population)


# --- on-the-fly ranking (API slice 3d; D-020 decisions 4 and B) ---------------------------------

MIN_MINUTES_FLOOR: Final = 90  # the lowest threshold the API accepts; also what a player
# ranked from outside a group needs on a per-90 metric (one full match; user, 2026-11-22)


@dataclass(frozen=True, slots=True)
class LiveGroup:
    """One peer group at any minimum minutes, with the sorted eligible values of its members
    per metric: the same rules and formula as the stored percentiles, nothing written."""

    peer_role: str
    min_minutes: int
    members: frozenset[int]  # player ids: the role and at least `min_minutes`
    populations: dict[str, list[float]]  # metric code -> members' eligible values, sorted

    @property
    def size(self) -> int:
        return len(self.members)

    def rank(self, code: str, value: float) -> tuple[float, int] | None:
        """(percentile, ranked among) of a value on a metric, against the members' eligible
        values. A member's own value is part of that population (as when stored); a player
        from outside is ranked against the members only and never added to them."""
        population = self.populations.get(code)
        if not population:
            return None
        return midpoint_percentile(value, population), len(population)


def live_group(
    session: Session, scope_id: int, peer_role: str, min_minutes: int, codes: Sequence[str]
) -> LiveGroup:
    """The peer group of `peer_role` at `min_minutes` on the given metrics (may be empty)."""
    members = frozenset(
        session.scalars(
            select(PlayerScope.player_id).where(
                PlayerScope.scope_id == scope_id,
                PlayerScope.peer_role == peer_role,
                PlayerScope.minutes >= min_minutes,
            )
        )
    )
    metrics = {m.id: m for m in session.scalars(select(Metric).where(Metric.code.in_(list(codes))))}
    populations: dict[str, list[float]] = {m.code: [] for m in metrics.values()}
    if members and metrics:
        for stat in session.scalars(
            select(PlayerScopeStat).where(
                PlayerScopeStat.scope_id == scope_id,
                PlayerScopeStat.player_id.in_(list(members)),
                PlayerScopeStat.metric_id.in_(list(metrics)),
            )
        ):
            metric = metrics[stat.metric_id]
            value = _eligible_value(stat, metric, min_minutes)
            if value is not None:
                populations[metric.code].append(value)
    for values in populations.values():
        values.sort()
    return LiveGroup(peer_role, min_minutes, members, populations)
