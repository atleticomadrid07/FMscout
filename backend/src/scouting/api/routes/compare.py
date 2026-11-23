"""Compare 2-6 players side by side (slice 3d; D-020).

Each player is ranked in his own peer group, or everyone against one group (`against=ROLE`).
A player who isn't a member of the group he's ranked against (another role, or under its
minutes) is ranked against its members without being added to it, flagged `outside_group`
(D-011 5, D-014; user 2026-11-22: "don't add his stats to the group"). The metrics come from
`metrics=`, `template=`, or by default the first player's position template."""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Query

from scouting.api.deps import SessionDep, aggregated_scope, player_in_scope
from scouting.api.errors import SCOPED_ERROR_RESPONSES, ApiError
from scouting.api.mapping import (
    MAX_METRICS,
    basis,
    check_role,
    find_template,
    group_out,
    metric_codes,
    provenance,
    rate_scale,
)
from scouting.api.ranking import custom_threshold, rank_in
from scouting.api.schemas import (
    CompareMetricOut,
    CompareOut,
    ComparePlayerOut,
    CompareTemplateOut,
    CompareValueOut,
)
from scouting.db.queries import GroupInfo, PlayerSeason, SeasonStatLine
from scouting.metrics.percentiles import MIN_MINUTES_FLOOR, LiveGroup, live_group
from scouting.metrics.registry import BY_CODE
from scouting.metrics.templates import Template

router = APIRouter(tags=["compare"], responses=SCOPED_ERROR_RESPONSES)

MIN_PLAYERS: Final = 2
MAX_PLAYERS: Final = 6


def _player_ids(players: str) -> list[str]:
    ids = [p.strip() for p in players.split(",") if p.strip()]
    if len(set(ids)) != len(ids):
        raise ApiError(400, "BAD_REQUEST", "players: each player once")
    if not MIN_PLAYERS <= len(ids) <= MAX_PLAYERS:
        raise ApiError(
            400, "BAD_REQUEST", f"players: {MIN_PLAYERS} to {MAX_PLAYERS} ids (got {len(ids)})"
        )
    return ids


def value_out(
    line: SeasonStatLine | None, group: LiveGroup | None, member: bool, why: str | None
) -> CompareValueOut:
    if line is None:
        return CompareValueOut(
            value=None, total=None, attempts=None, minutes=0, pct=None, ranked_among=None,
            reason="no data",
        )  # fmt: skip
    metric = BY_CODE[line.code]
    if group is None:
        pct, ranked_among, reason = None, None, why
    else:
        ranked = rank_in(line, group, member=member)
        pct, ranked_among, reason = ranked.pct, ranked.ranked_among, ranked.reason
    return CompareValueOut(
        value=line.rate if metric.is_fraction else line.per90,
        total=None if metric.value_is_rate else line.total,
        attempts=line.denominator if metric.is_fraction else None,
        minutes=line.minutes,
        pct=pct,
        ranked_among=ranked_among,
        reason=reason,
    )


@router.get("/compare")
def compare(
    session: SessionDep,
    scope: Annotated[str, Query(description="A scope key from /scopes")],
    players: Annotated[str, Query(description="2-6 comma-separated player ids")],
    metrics: Annotated[str | None, Query(description="Comma-separated metric codes")] = None,
    template: Annotated[
        str | None,
        Query(description="A template id or role; default: the first player's position"),
    ] = None,
    against: Annotated[
        str | None, Query(description="Rank everyone against this role's group")
    ] = None,
    min_minutes: Annotated[
        int | None,
        Query(ge=MIN_MINUTES_FLOOR, description="The groups' threshold; default: the stored one"),
    ] = None,
) -> CompareOut:
    """Players side by side on the same metrics, each with value and percentile."""
    ids = _player_ids(players)
    if metrics is not None and template is not None:
        raise ApiError(400, "BAD_REQUEST", "pass metrics= or template=, not both")
    if against is not None:
        check_role(against)
    codes = metric_codes(metrics)
    summary = aggregated_scope(session, scope)
    assert summary.scope_id is not None and summary.min_minutes is not None
    scope_id = summary.scope_id
    seasons: list[PlayerSeason] = [player_in_scope(session, summary, pid)[1] for pid in ids]

    chosen: Template | None = None
    if codes is None:
        if template is not None:
            chosen = find_template(template)
        elif seasons[0].peer_role is not None:
            chosen = find_template(seasons[0].peer_role)
        else:
            raise ApiError(
                400,
                "TEMPLATE_REQUIRED",
                f"{seasons[0].external_id} (listed first) has no peer role: pass template= "
                "or metrics=",
            )
        codes = chosen.codes
    if not codes:
        raise ApiError(400, "BAD_REQUEST", "metrics: give at least one metric code")
    if len(codes) > MAX_METRICS:
        raise ApiError(400, "BAD_REQUEST", f"metrics: at most {MAX_METRICS} (got {len(codes)})")

    threshold = custom_threshold(summary, min_minutes)
    level = summary.min_minutes if threshold is None else threshold
    groups: dict[str, LiveGroup] = {}

    def group_for(role: str) -> LiveGroup:
        if role not in groups:
            groups[role] = live_group(session, scope_id, role, level, codes)
        return groups[role]

    out = []
    for season in seasons:
        role = against or season.peer_role
        lines = {line.code: line for line in season.stats}
        group = None if role is None else group_for(role)
        why = None
        if role is None:
            why = "no peer role"
        elif group is not None and group.size == 0:
            why, group = f"no {role} players with at least {level} min", None
        member = group is not None and season.peer_role == role and season.minutes >= level
        out.append(
            ComparePlayerOut(
                id=season.external_id,
                name=season.name,
                team=season.teams[0][0] if season.teams else None,
                minutes=season.minutes,
                peer_role=season.peer_role,
                group=None
                if group is None
                else group_out(
                    GroupInfo(group.peer_role, group.size, level), stored=threshold is None
                ),
                outside_group=group is not None and not member,
                values={code: value_out(lines.get(code), group, member, why) for code in codes},
            )
        )
    return CompareOut(
        provenance=provenance(summary),
        template=None
        if chosen is None
        else CompareTemplateOut(id=chosen.id, label=chosen.label, peer_role=chosen.peer_role),
        against=against,
        min_minutes=level,
        stored=threshold is None,
        metrics=[
            CompareMetricOut(
                metric=code,
                label=BY_CODE[code].label,
                basis=basis(BY_CODE[code]),
                rate_scale=rate_scale(BY_CODE[code]),
                model=BY_CODE[code].is_model_output,
                direction=BY_CODE[code].direction,
            )
            for code in codes
        ],
        players=out,
    )
