"""Players: search, the player card, the stat table and per-match values (slice 3b)."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Query, Request

from scouting.api.deps import SessionDep, aggregated_scope, player_in_scope, search_index
from scouting.api.errors import SCOPED_ERROR_RESPONSES, ApiError
from scouting.api.schemas import (
    GroupOut,
    MatchValueOut,
    PlayerCoverageOut,
    PlayerMatchesOut,
    PlayerOut,
    PlayerRefOut,
    ProvenanceOut,
    RatingOut,
    RoleStartsOut,
    SearchHitOut,
    SearchOut,
    SixOrEightOut,
    StatLineOut,
    StatsOut,
    TeamMinutesOut,
)
from scouting.db.queries import (
    GroupInfo,
    PlayerSeason,
    ScopeSummary,
    SeasonStatLine,
    player_extras,
    player_matches,
)
from scouting.domain.positions import PEER_ROLES
from scouting.metrics import depth as depth_rules
from scouting.metrics.percentiles import SMALL_GROUP
from scouting.metrics.registry import BY_CODE, MetricDef
from scouting.metrics.templates import missing_reason

router = APIRouter(prefix="/players", tags=["players"], responses=SCOPED_ERROR_RESPONSES)

ScopeKey = Annotated[str, Query(description="A scope key from /scopes, e.g. l_4WFCIZ.2025-2026")]


# --- mapping ---------------------------------------------------------------------------------


def group_out(group: GroupInfo | None, *, stored: bool = True) -> GroupOut | None:
    if group is None:
        return None
    return GroupOut(
        role=group.peer_role,
        size=group.size,
        min_minutes=group.min_minutes,
        small_sample=group.size < SMALL_GROUP,
        stored=stored,
    )


def provenance(scope: ScopeSummary) -> ProvenanceOut:
    return ProvenanceOut(
        source="pitchapi",
        scope=scope.key,
        competition=scope.competition,
        season=scope.season,
        computed_at=scope.computed_at,
    )


RateScale = Literal["share", "percent", "metres"]


def rate_scale(metric: MetricDef) -> RateScale | None:
    """A success rate is a 0-1 share; an averaged rate keeps its unit (D-020)."""
    if not metric.is_fraction:
        return None
    if not metric.value_is_rate:
        return "share"
    return "metres" if metric.unit == "m" else "percent"


def stat_line_out(line: SeasonStatLine, season: PlayerSeason) -> StatLineOut:
    metric = BY_CODE[line.code]
    return StatLineOut(
        metric=line.code,
        label=metric.label,
        kind=line.kind,
        basis="rate" if metric.is_fraction else "per90",
        model=line.is_model_output,
        direction=metric.direction,
        total=None if metric.value_is_rate else line.total,
        attempts=line.denominator if metric.is_fraction else None,
        per90=line.per90,
        rate=line.rate,
        rate_scale=rate_scale(metric),
        minutes=line.minutes,
        matches=line.matches,
        pct=line.pct,
        ranked_among=line.ranked_among,
        reason=None if line.pct is not None else missing_reason(line, season),
    )


def _role_source(value: str | None) -> Literal["starts", "listed"] | None:
    return cast("Literal['starts', 'listed']", value) if value in ("starts", "listed") else None


def _six_or_eight(season: PlayerSeason) -> SixOrEightOut | None:
    """How a central mid was put with the 6s or the 8s (D-017, D-018); None for other roles."""
    if season.peer_method not in ("depth", "partner", "formation"):
        return None
    if season.peer_role not in depth_rules.CENTRAL_MID_ROLES:
        return None
    m = depth_rules.PITCH_LENGTH_M
    return SixOrEightOut(
        group=cast("depth_rules.Label", season.peer_role),
        method=cast("depth_rules.Method", season.peer_method),
        depth_m=None if season.depth is None else round(season.depth * m, 2),
        depth_starts=season.depth_starts or 0,
        partner_gap_m=None if season.partner_gap is None else round(season.partner_gap * m, 2),
        partner_starts=season.partner_starts or 0,
        borderline=season.depth is not None and depth_rules.is_borderline(season.depth),
        cut_m=depth_rules.DEPTH_CUT_M,
        partner_gap_rule_m=depth_rules.PARTNER_GAP_M,
        min_starts=depth_rules.MIN_DEPTH_STARTS,
    )


def _match_rate(value: float | None, attempts: float | None) -> float | None:
    """value / attempts: a success rate (0-1), or an averaged rate stored as rate x weight."""
    if value is None or not attempts:
        return None
    return value / attempts


def _codes(metrics: str | None) -> list[str] | None:
    if metrics is None:
        return None
    codes = [c.strip() for c in metrics.split(",") if c.strip()]
    unknown = [c for c in codes if c not in BY_CODE]
    if unknown:
        raise ApiError(404, "UNKNOWN_METRIC", f"unknown metrics: {', '.join(unknown)}")
    return codes


# --- routes ----------------------------------------------------------------------------------


@router.get("/search")
def search_players(
    request: Request,
    session: SessionDep,
    scope: ScopeKey,
    q: Annotated[str, Query(min_length=2, max_length=100, description="A name, or part of one")],
    team: Annotated[str | None, Query(description="Part of the team's name")] = None,
    role: Annotated[str | None, Query(description="A peer role: GK CB FB DM CM AM W ST")] = None,
    min_minutes: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchOut:
    """Accent-free fuzzy name search within one season (exact name first)."""
    if role is not None and role not in PEER_ROLES:
        raise ApiError(
            400, "UNKNOWN_ROLE", f"unknown role {role!r}; one of {', '.join(PEER_ROLES)}"
        )
    summary = aggregated_scope(session, scope)
    hits = search_index(request, session, summary).search(
        q, team=team, role=role, min_minutes=min_minutes, limit=limit
    )
    return SearchOut(
        query=q,
        scope=summary.key,
        results=[
            SearchHitOut(
                id=h.entry.external_id,
                name=h.entry.name,
                short_name=h.entry.short_name,
                team=h.entry.team,
                peer_role=h.entry.peer_role,
                minutes=h.entry.minutes,
                score=round(h.score, 1),
                exact=h.exact,
            )
            for h in hits
        ],
    )


@router.get("/{player_id}")
def get_player(player_id: str, session: SessionDep, scope: ScopeKey) -> PlayerOut:
    """The player card: who, where, how much, which group, and what data is missing."""
    summary = aggregated_scope(session, scope)
    internal_id, season = player_in_scope(session, summary, player_id)
    extras = player_extras(session, summary.season_id, internal_id)
    return PlayerOut(
        id=season.external_id,
        name=season.name,
        short_name=extras.short_name,
        country_code=extras.country_code,
        image_url=extras.image_url,
        provenance=provenance(summary),
        minutes=season.minutes,
        matches=season.matches,
        starts=season.starts,
        teams=[TeamMinutesOut(name=t, minutes=m, matches=n) for t, m, n in season.teams],
        primary_role=season.primary_role,
        role_source=_role_source(season.role_source),
        starts_by_role=[RoleStartsOut(role=r, minutes=m, starts=n) for r, m, n in season.roles],
        peer_role=season.peer_role,
        six_or_eight=_six_or_eight(season),
        rating=RatingOut(average=season.avg_rating, rated_matches=season.rated_matches),
        group=group_out(season.group),
        not_ranked_reason=None if season.group is not None else missing_reason(None, season),
        coverage=PlayerCoverageOut(
            matches=season.matches,
            without_advanced=extras.matches_without_advanced,
            without_heatmap=extras.matches_without_heatmap,
        ),
    )


@router.get("/{player_id}/stats")
def get_player_stats(
    player_id: str,
    session: SessionDep,
    scope: ScopeKey,
    metrics: Annotated[str | None, Query(description="Comma-separated metric codes")] = None,
) -> StatsOut:
    """The full stat table (or the chosen metrics, in the order given)."""
    codes = _codes(metrics)
    summary = aggregated_scope(session, scope)
    _, season = player_in_scope(session, summary, player_id)
    lines = {line.code: line for line in season.stats}
    order = codes if codes is not None else [line.code for line in season.stats]
    return StatsOut(
        player=PlayerRefOut(id=season.external_id, name=season.name),
        provenance=provenance(summary),
        minutes=season.minutes,
        group=group_out(season.group),
        stats=[stat_line_out(lines[c], season) for c in order if c in lines],
        missing=[c for c in order if c not in lines],
    )


@router.get("/{player_id}/matches")
def get_player_matches(
    player_id: str,
    session: SessionDep,
    scope: ScopeKey,
    metric: Annotated[str, Query(description="One metric code")],
) -> PlayerMatchesOut:
    """Every appearance, oldest first, with one metric's stored value (null = no data)."""
    definition = BY_CODE.get(metric)
    if definition is None:
        raise ApiError(404, "UNKNOWN_METRIC", f"unknown metric {metric!r}")
    stored = definition.volume_of or metric
    summary = aggregated_scope(session, scope)
    internal_id, season = player_in_scope(session, summary, player_id)
    rows = player_matches(session, summary.season_id, internal_id, stored)
    fraction, averaged = BY_CODE[stored].is_fraction, BY_CODE[stored].value_is_rate
    return PlayerMatchesOut(
        player=PlayerRefOut(id=season.external_id, name=season.name),
        provenance=provenance(summary),
        metric=metric,
        stored_metric=stored,
        basis="rate" if definition.is_fraction else "per90",
        rate_scale=rate_scale(BY_CODE[stored]),
        matches=[
            MatchValueOut(
                match_id=r.match_id,
                date=r.match_date,
                team=r.team,
                opponent=r.opponent,
                venue="home" if r.home else "away",
                goals_for=r.goals_for,
                goals_against=r.goals_against,
                minutes=r.minutes,
                started=r.started,
                value=None if averaged else r.value,
                attempts=r.denominator if fraction else None,
                rate=_match_rate(r.value, r.denominator) if fraction else None,
            )
            for r in rows
        ],
    )
