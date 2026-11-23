"""Percentiles at a non-default minimum minutes, and ranking a player from outside a group
(slice 3d; D-020). Same eligibility rules and midpoint formula as the stored percentiles,
computed per request with metrics.percentiles.live_group; nothing is written."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from scouting.db.queries import GroupInfo, PlayerSeason, ScopeSummary, SeasonStatLine
from scouting.metrics.percentiles import MIN_MINUTES_FLOOR, LiveGroup, eligible_value, live_group
from scouting.metrics.templates import stat_reason


def custom_threshold(scope: ScopeSummary, min_minutes: int | None) -> int | None:
    """The threshold to compute on the fly, or None to use the stored percentiles (no
    `min_minutes` given, or the stored one)."""
    if min_minutes is None or min_minutes == scope.min_minutes:
        return None
    return min_minutes


def line_value(line: SeasonStatLine, min_minutes: int) -> float | None:
    """His eligible value on a metric at a threshold (None: not eligible)."""
    return eligible_value(
        is_fraction=line.kind == "fraction",
        rate=line.rate,
        per90=line.per90,
        denominator=line.denominator,
        minutes=line.minutes,
        min_attempts=line.min_attempts,
        min_minutes=min_minutes,
    )


def season_at(
    session: Session, scope: ScopeSummary, season: PlayerSeason, min_minutes: int
) -> PlayerSeason:
    """His season with group and percentiles recomputed at `min_minutes` (own peer role)."""
    assert scope.scope_id is not None
    unranked = [replace(line, pct=None, ranked_among=None) for line in season.stats]
    if season.peer_role is None or season.minutes < min_minutes:
        return replace(season, group=None, group_min_minutes=min_minutes, stats=unranked)
    group = live_group(
        session, scope.scope_id, season.peer_role, min_minutes, [line.code for line in season.stats]
    )
    stats = []
    for line in season.stats:
        value = line_value(line, min_minutes)
        ranked = group.rank(line.code, value) if value is not None else None
        stats.append(
            replace(
                line,
                pct=None if ranked is None else ranked[0],
                ranked_among=None if ranked is None else ranked[1],
            )
        )
    return replace(
        season,
        group=GroupInfo(season.peer_role, group.size, min_minutes),
        group_min_minutes=min_minutes,
        stats=stats,
    )


@dataclass(frozen=True, slots=True)
class Ranked:
    pct: float | None
    ranked_among: int | None
    reason: str | None  # why pct is None


def rank_in(line: SeasonStatLine | None, group: LiveGroup, *, member: bool) -> Ranked:
    """One metric of one player against a group. A member needs the group's eligibility; a
    player from outside is ranked against the members only (never added: user, D-020 B),
    needing the metric's minimum attempts on rates and, on per-90s, MIN_MINUTES_FLOOR (or
    the group's own threshold if that's lower: never more than the members need)."""
    if line is None:
        return Ranked(None, None, "no data")
    threshold = group.min_minutes if member else min(group.min_minutes, MIN_MINUTES_FLOOR)
    value = line_value(line, threshold)
    if value is None:
        reason = stat_reason(
            is_fraction=line.kind == "fraction",
            min_attempts=line.min_attempts,
            attempts=line.denominator,
            minutes=line.minutes,
            group_min_minutes=threshold,
        )
        return Ranked(None, None, reason)
    ranked = group.rank(line.code, value)
    if ranked is None:
        return Ranked(None, None, f"nobody in the {group.peer_role} group ranks on it")
    return Ranked(ranked[0], ranked[1], None)
