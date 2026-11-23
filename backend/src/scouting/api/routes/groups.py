"""Peer groups: every member on chosen metrics, for rankings, bars and scatters (slice 3c)."""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy.orm import Session

from scouting.api.deps import SessionDep, aggregated_scope
from scouting.api.errors import SCOPED_ERROR_RESPONSES, ApiError
from scouting.api.mapping import (
    MAX_METRICS,
    basis,
    check_role,
    group_out,
    metric_codes,
    provenance,
    rate_scale,
)
from scouting.api.ranking import custom_threshold
from scouting.api.schemas import (
    GroupMetricOut,
    GroupPlayerOut,
    GroupValueOut,
    GroupViewOut,
)
from scouting.db.queries import GroupMember, GroupStat, GroupView, group_view
from scouting.metrics.percentiles import MIN_MINUTES_FLOOR, eligible_value, live_group
from scouting.metrics.registry import BY_CODE, MetricDef
from scouting.metrics.templates import builtin_templates, stat_reason

router = APIRouter(prefix="/groups", tags=["groups"], responses=SCOPED_ERROR_RESPONSES)


def value_out(stat: GroupStat | None, metric: MetricDef, group_min_minutes: int) -> GroupValueOut:
    if stat is None:
        return GroupValueOut(
            value=None, total=None, attempts=None, minutes=0, pct=None, reason="no data"
        )
    value = stat.rate if metric.is_fraction else stat.per90
    return GroupValueOut(
        value=value,
        total=None if metric.value_is_rate else stat.total,
        attempts=stat.denominator if metric.is_fraction else None,
        minutes=stat.minutes,
        pct=stat.pct,
        reason=None
        if stat.pct is not None
        else stat_reason(
            is_fraction=metric.is_fraction,
            min_attempts=metric.min_attempts,
            attempts=stat.denominator,
            minutes=stat.minutes,
            group_min_minutes=group_min_minutes,
        ),
    )


def ranked_live(session: Session, scope_id: int, view: GroupView, codes: list[str]) -> GroupView:
    """The members' percentiles at the view's (non-default) threshold, computed on the fly."""
    live = live_group(session, scope_id, view.group.peer_role, view.group.min_minutes, codes)
    members = []
    for member in view.members:
        stats = {}
        for code, stat in member.stats.items():
            metric = BY_CODE[code]
            value = eligible_value(
                is_fraction=metric.is_fraction,
                rate=stat.rate,
                per90=stat.per90,
                denominator=stat.denominator,
                minutes=stat.minutes,
                min_attempts=metric.min_attempts,
                min_minutes=view.group.min_minutes,
            )
            ranked = live.rank(code, value) if value is not None else None
            stats[code] = replace(
                stat,
                pct=None if ranked is None else ranked[0],
                ranked_among=None if ranked is None else ranked[1],
            )
        members.append(replace(member, stats=stats))
    return replace(view, members=members)


def sort_members(members: list[GroupMember], metric: MetricDef) -> list[GroupMember]:
    """Best first on one metric (lowest first when lower is better), no value last, then by
    name. A metric with no direction sorts highest first."""
    sign = 1.0 if metric.direction == "lower" else -1.0

    def key(member: GroupMember) -> tuple[bool, float, str]:
        stat = member.stats.get(metric.code)
        value = None if stat is None else (stat.rate if metric.is_fraction else stat.per90)
        return (value is None, 0.0 if value is None else sign * value, member.name)

    return sorted(members, key=key)


@router.get("/{role}")
def get_group(
    role: str,
    session: SessionDep,
    scope: Annotated[str, Query(description="A scope key from /scopes")],
    metrics: Annotated[
        str | None,
        Query(description="Comma-separated metric codes; default: the role's template"),
    ] = None,
    sort: Annotated[str | None, Query(description="One of the metrics; default: the first")] = None,
    min_minutes: Annotated[
        int | None,
        Query(ge=MIN_MINUTES_FLOOR, description="A different threshold, computed on the fly"),
    ] = None,
) -> GroupViewOut:
    """Every member of the stored peer group (the role, at least the group's minutes), each
    with value and percentile on the chosen metrics. Players below the minutes threshold
    aren't members. The client picks which points to label on a scatter."""
    check_role(role)
    codes = metric_codes(metrics)
    if codes is None:
        codes = builtin_templates()[role].codes
    if not codes:
        raise ApiError(400, "BAD_REQUEST", "metrics: give at least one metric code")
    if len(codes) > MAX_METRICS:
        raise ApiError(400, "BAD_REQUEST", f"metrics: at most {MAX_METRICS} (got {len(codes)})")
    sort_code = sort or codes[0]
    if sort_code not in codes:
        raise ApiError(400, "BAD_REQUEST", f"sort: {sort_code!r} isn't one of the metrics asked")
    summary = aggregated_scope(session, scope)
    assert summary.scope_id is not None  # aggregated_scope checked it
    threshold = custom_threshold(summary, min_minutes)
    view = group_view(session, summary.scope_id, role, codes, threshold)
    if view is None or not view.members:
        at = "" if threshold is None else f" with at least {threshold} min"
        raise ApiError(404, "GROUP_NOT_FOUND", f"{summary.key} has no {role} peer group{at}")
    if threshold is not None:
        view = ranked_live(session, summary.scope_id, view, codes)
    defs = [BY_CODE[c] for c in codes]
    group = group_out(view.group, stored=threshold is None)
    assert group is not None
    return GroupViewOut(
        provenance=provenance(summary),
        group=group,
        metrics=[
            GroupMetricOut(
                metric=m.code,
                label=m.label,
                basis=basis(m),
                rate_scale=rate_scale(m),
                model=m.is_model_output,
                direction=m.direction,
                ranked_among=sum(
                    1
                    for member in view.members
                    if (s := member.stats.get(m.code)) is not None and s.pct is not None
                ),
            )
            for m in defs
        ],
        sort=sort_code,
        players=[
            GroupPlayerOut(
                id=member.external_id,
                name=member.name,
                team=member.team,
                minutes=member.minutes,
                values={
                    m.code: value_out(member.stats.get(m.code), m, view.group.min_minutes)
                    for m in defs
                },
            )
            for member in sort_members(view.members, BY_CODE[sort_code])
        ],
    )
