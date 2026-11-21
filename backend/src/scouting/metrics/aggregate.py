"""Season aggregates (Phase 2, slice 2a; SCHEMA §6): totals, exposure minutes, per-90s and
rates per player per metric, plus each player's minutes, primary role and rating.

Reads only the canonical match-level tables and rebuilds one scope in one transaction
(delete, then insert), so a re-run gives identical rows and `report checksum` stays stable.

Rules:
- Exposure is per metric: the minutes of the appearances that counted for it. A match with
  no row for a metric (it doesn't apply, or its source had no data) adds no minutes.
- A stored NULL counts per the registry's `season_null`: "skip" (unknown: no total, no
  minutes) or "zero" (a known zero: adds 0 and its minutes).
- per90 = total / minutes x 90 for metrics that allow it; rate = total / denominator for
  fractions (the exact weighted average for value_is_rate metrics). NULL when undefined.
- Primary role: most minutes as a starter (ties: more starts, then ROLES order). With no
  starts, the profile's listed slot where every formation agrees on it (positions.py).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from sqlalchemy import CursorResult, bindparam, case, delete, func, select, text
from sqlalchemy.orm import Session

from scouting.db.models import (
    Appearance,
    Match,
    Metric,
    Player,
    PlayerScope,
    PlayerScopeRole,
    PlayerScopeStat,
    PlayerScopeTeam,
    Scope,
    Season,
)
from scouting.domain.positions import PEER_ROLE, ROLES, PeerRole, Role, listed_slot_roles
from scouting.metrics.percentiles import PercentileSummary, build_percentiles
from scouting.metrics.registry import REGISTRY

MINUTES_PER_MATCH: Final = 90
DEFAULT_MIN_MINUTES_SHARE: Final = 0.25  # D-011: 25% of the possible minutes


class AggregateError(Exception):
    """The scope can't be aggregated (e.g. no normalized matches yet)."""


@dataclass(slots=True)
class AggregateSummary:
    season: str
    players: int = 0
    stat_rows: int = 0
    min_minutes: int = 0
    # Peer-role sizes among players with at least min_minutes; key None = no role.
    peer_roles: Counter[str | None] = field(default_factory=Counter)
    role_sources: Counter[str | None] = field(default_factory=Counter)
    percentiles: PercentileSummary | None = None


def scope_for_season(session: Session, season_id: int) -> Scope:
    """The season's scope, created on first use. Its ID then stays stable across rebuilds."""
    scope = session.scalars(select(Scope).where(Scope.season_id == season_id)).one_or_none()
    if scope is None:
        scope = Scope(kind="season", season_id=season_id)
        session.add(scope)
        session.flush()
    return scope


def default_min_minutes(session: Session, season_id: int) -> int:
    """25% of the possible minutes, scaled to matches played so far (D-011).

    "Possible" uses the most matches any team has played, so it equals 38 x 90 for a
    completed 20-team season (855 minutes) and grows during a season in progress.
    """
    home = select(Match.home_team_id.label("team_id")).where(Match.season_id == season_id)
    away = select(Match.away_team_id.label("team_id")).where(Match.season_id == season_id)
    sides = home.union_all(away).subquery()
    per_team = (
        select(func.count().label("n")).select_from(sides).group_by(sides.c.team_id).subquery()
    )
    most = session.scalar(select(func.max(per_team.c.n))) or 0
    return round(most * MINUTES_PER_MATCH * DEFAULT_MIN_MINUTES_SHARE)


# One INSERT ... SELECT over stat_value: SQLite does the grouping, so 850k rows per season
# never pass through Python. `counted` decides whether a row adds to total and minutes.
_STAT_ROWS = text(
    """
    INSERT INTO player_scope_stat
        (player_id, scope_id, metric_id, total, denominator, minutes,
         matches_with_value, per90, rate)
    SELECT player_id, :scope_id, metric_id, total, denominator, minutes, matches,
           CASE WHEN per90_allowed AND minutes > 0 AND total IS NOT NULL
                THEN total * 90.0 / minutes END,
           CASE WHEN aggregation = 'rate_from_sums' AND denominator > 0
                THEN total / denominator END
    FROM (
        SELECT a.player_id AS player_id,
               sv.metric_id AS metric_id,
               m.per90_allowed AS per90_allowed,
               m.aggregation AS aggregation,
               SUM(CASE WHEN sv.value IS NOT NULL OR sv.metric_id IN :zero_ids
                        THEN COALESCE(sv.value, 0) END) AS total,
               SUM(CASE WHEN sv.value IS NOT NULL OR sv.metric_id IN :zero_ids
                        THEN sv.denominator END) AS denominator,
               COALESCE(SUM(CASE WHEN sv.value IS NOT NULL OR sv.metric_id IN :zero_ids
                                 THEN a.minutes END), 0) AS minutes,
               SUM(CASE WHEN sv.value IS NOT NULL OR sv.metric_id IN :zero_ids
                        THEN 1 ELSE 0 END) AS matches
        FROM stat_value AS sv
        JOIN appearance AS a ON a.id = sv.appearance_id
        JOIN "match" AS mt ON mt.id = a.match_id
        JOIN metric AS m ON m.id = sv.metric_id
        WHERE mt.season_id = :season_id
        GROUP BY a.player_id, sv.metric_id
    )
    """
).bindparams(bindparam("zero_ids", expanding=True))


_ROLE_ORDER: Final[dict[str, int]] = {role: i for i, role in enumerate(ROLES)}


def _primary_role(roles: list[PlayerScopeRole]) -> Role | None:
    if not roles:
        return None
    best = max(roles, key=lambda r: (r.minutes, r.starts, -_ROLE_ORDER[r.role]))
    return next(role for role in ROLES if role == best.role)


def aggregate_season(session: Session, season_id: int) -> AggregateSummary:
    """Rebuild every aggregate for one competition-season. Call inside a transaction."""
    season = session.get_one(Season, season_id)
    computed_at: datetime | None = session.scalar(
        select(func.max(Match.normalized_at)).where(Match.season_id == season_id)
    )
    if computed_at is None:
        raise AggregateError(f"season {season.label} has no synced matches to aggregate")
    scope = scope_for_season(session, season_id)
    summary = AggregateSummary(season=season.label)

    # Percentile rows reference players; build_percentiles clears and rebuilds them below.
    for table in (PlayerScopeStat, PlayerScopeRole, PlayerScopeTeam, PlayerScope):
        session.execute(delete(table).where(table.scope_id == scope.id))

    in_season = (
        select(Appearance)
        .join(Match, Match.id == Appearance.match_id)
        .where(Match.season_id == season_id)
        .subquery()
    )
    a = in_season.c

    # Minutes per team and per starting role.
    for row in session.execute(
        select(a.player_id, a.team_id, func.sum(a.minutes), func.count())
        .group_by(a.player_id, a.team_id)
    ):  # fmt: skip
        session.add(
            PlayerScopeTeam(
                player_id=row[0], scope_id=scope.id, team_id=row[1], minutes=row[2], matches=row[3]
            )
        )
    roles_by_player: dict[int, list[PlayerScopeRole]] = {}
    for row in session.execute(
        select(a.player_id, a.role, func.sum(a.minutes), func.count())
        .where(a.is_starter.is_(True), a.role.is_not(None))
        .group_by(a.player_id, a.role)
    ):
        role_row = PlayerScopeRole(
            player_id=row[0], scope_id=scope.id, role=row[1], minutes=row[2], starts=row[3]
        )
        session.add(role_row)
        roles_by_player.setdefault(row[0], []).append(role_row)

    # One row per player: minutes, starts, role, rating.
    listed = {
        pid: slot for pid, slot in session.execute(select(Player.id, Player.listed_slot_code))
    }
    min_minutes = default_min_minutes(session, season_id)
    starts = func.sum(case((a.is_starter.is_(True), 1), else_=0))
    for player_id, minutes, matches, n_starts, avg_rating, rated in session.execute(
        select(
            a.player_id, func.sum(a.minutes), func.count(), starts,
            func.avg(a.rating), func.count(a.rating),
        ).group_by(a.player_id)
    ):  # fmt: skip
        role = _primary_role(roles_by_player.get(player_id, []))
        primary: Role | None
        peer: PeerRole | None
        source: str | None
        if role is not None:
            primary, peer, source = role, PEER_ROLE[role], "starts"
        else:
            primary, peer = listed_slot_roles(listed.get(player_id))
            source = "listed" if peer is not None else None
        session.add(
            PlayerScope(
                player_id=player_id, scope_id=scope.id, minutes=minutes, matches=matches,
                starts=n_starts, primary_role=primary, peer_role=peer, role_source=source,
                avg_rating=avg_rating, rated_matches=rated, computed_at=computed_at,
            )
        )  # fmt: skip
        summary.players += 1
        summary.role_sources[source] += 1
        if minutes >= min_minutes:
            summary.peer_roles[peer] += 1
    session.flush()

    zero_ids = list(
        session.scalars(
            select(Metric.id).where(
                Metric.code.in_([m.code for m in REGISTRY if m.season_null == "zero"])
            )
        )
    ) or [-1]  # an empty IN list isn't valid SQLite
    result = session.execute(
        _STAT_ROWS, {"scope_id": scope.id, "season_id": season_id, "zero_ids": zero_ids}
    )
    assert isinstance(result, CursorResult)  # an INSERT always gives a cursor result
    summary.stat_rows = result.rowcount
    summary.min_minutes = min_minutes
    summary.percentiles = build_percentiles(session, scope.id, min_minutes, computed_at)
    return summary
