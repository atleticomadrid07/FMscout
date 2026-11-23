"""Shared mapping from the read layer's dataclasses to response models (slices 3b, 3c)."""

from __future__ import annotations

from typing import Final, Literal

from scouting.api.errors import ApiError
from scouting.api.schemas import GroupOut, ProvenanceOut, StatLineOut
from scouting.db.queries import GroupInfo, PlayerSeason, ScopeSummary, SeasonStatLine
from scouting.domain.positions import PEER_ROLES
from scouting.metrics.percentiles import SMALL_GROUP
from scouting.metrics.registry import BY_CODE, MetricDef
from scouting.metrics.templates import Template, builtin_templates, missing_reason

RateScale = Literal["share", "percent", "metres"]
MAX_METRICS: Final = 14  # per request: the largest template size (D-019), so any template fits


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


def basis(metric: MetricDef) -> Literal["per90", "rate"]:
    return "rate" if metric.is_fraction else "per90"


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
        basis=basis(metric),
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


def empty_line_out(code: str, season: PlayerSeason) -> StatLineOut:
    """A template row for a metric the player has no data for (every number null)."""
    metric = BY_CODE[code]
    return StatLineOut(
        metric=code,
        label=metric.label,
        kind=metric.kind,
        basis=basis(metric),
        model=metric.is_model_output,
        direction=metric.direction,
        total=None,
        attempts=None,
        per90=None,
        rate=None,
        rate_scale=rate_scale(metric),
        minutes=0,
        matches=0,
        pct=None,
        ranked_among=None,
        reason=missing_reason(None, season),
    )


def metric_codes(metrics: str | None) -> list[str] | None:
    """A comma-separated `metrics=` parameter, in order, duplicates dropped; 404 on an
    unknown code."""
    if metrics is None:
        return None
    codes = list(dict.fromkeys(c.strip() for c in metrics.split(",") if c.strip()))
    unknown = [c for c in codes if c not in BY_CODE]
    if unknown:
        raise ApiError(404, "UNKNOWN_METRIC", f"unknown metrics: {', '.join(unknown)}")
    return codes


def check_role(role: str) -> str:
    if role not in PEER_ROLES:
        raise ApiError(
            400, "UNKNOWN_ROLE", f"unknown role {role!r}; one of {', '.join(PEER_ROLES)}"
        )
    return role


def find_template(key: str) -> Template:
    """A template by id (`builtin:ST`) or, as a shortcut, by peer role (`ST`)."""
    templates = builtin_templates()
    by_id = {t.id: t for t in templates.values()}
    template = by_id.get(key) or templates.get(key)
    if template is None:
        raise ApiError(404, "UNKNOWN_TEMPLATE", f"no template {key!r}; see /api/v1/templates")
    return template
