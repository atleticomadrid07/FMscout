"""Response models: the API contract. OpenAPI (and the Phase 4 TypeScript types) come from
these; tests/unit/api/openapi.json snapshots them so any change is deliberate."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class ErrorDetail(_Out):
    code: str
    message: str


class ErrorResponse(_Out):
    error: ErrorDetail


class Health(_Out):
    status: Literal["ok", "schema_out_of_date"]
    api_version: str
    schema_revision: str | None  # the database's Alembic revision
    message: str | None  # why the database isn't usable, when it isn't


class PeerGroupOut(_Out):
    role: str
    size: int
    min_minutes: int
    small_sample: bool  # under 20 players: percentiles are less reliable


class CoverageOut(_Out):
    """What the season's matches have, so gaps show as gaps (not blanks or zeros)."""

    matches: int
    players_missing: int
    advanced_unavailable: int  # no advanced analytics (xT, VAEP, progressive actions, ...)
    lineups_missing: int
    heatmaps_missing: int
    pending: int  # not fully synced yet
    matches_with_ratings: int  # 0 = a league without ratings


class ScopeOut(_Out):
    key: str  # e.g. "l_4WFCIZ.2025-2026": use it in every other request
    kind: Literal["season"]
    source: Literal["pitchapi"]
    competition: str
    country_code: str
    season: str
    is_current: bool
    aggregated: bool  # False: season stats and percentiles aren't built yet
    min_minutes: int | None  # the stored percentiles' minimum minutes
    computed_at: datetime | None
    coverage: CoverageOut
    peer_groups: list[PeerGroupOut]


class ScopesOut(_Out):
    scopes: list[ScopeOut]


class MetricOut(_Out):
    code: str
    label: str
    unit: str
    kind: str  # count, fraction, model, rating, distance
    source: str  # pitchapi_players or pitchapi_advanced
    basis: Literal["per90", "rate"]  # what its percentiles rank
    model: bool  # a model output (xG, xT, VAEP, ...), not a counted event
    direction: Literal["higher", "lower", "none"]  # "none": a style metric
    outcome: bool  # needs a goal or a penalty: never on position templates
    volume_of: str | None  # a derived volume metric's success-rate parent
    applies_to: Literal["all", "outfield", "gk"]
    min_attempts: int | None  # success rates: fewest season attempts for a percentile
    description: str


class MetricsOut(_Out):
    metrics: list[MetricOut]


class TemplateSectionOut(_Out):
    name: str
    metrics: list[str]


class TemplateOut(_Out):
    id: str
    peer_role: str
    label: str
    builtin: bool
    size: int
    sections: list[TemplateSectionOut]


class TemplatesOut(_Out):
    templates: list[TemplateOut]


# --- players (slice 3b) ------------------------------------------------------------------------


class GroupOut(_Out):
    """The peer group a player's percentiles come from."""

    role: str
    size: int
    min_minutes: int
    small_sample: bool
    stored: bool  # True: the stored percentiles; False: computed for a custom min_minutes


class ProvenanceOut(_Out):
    source: Literal["pitchapi"]
    scope: str
    competition: str
    season: str
    computed_at: datetime | None


class PlayerRefOut(_Out):
    id: str
    name: str


class SearchHitOut(_Out):
    id: str
    name: str
    short_name: str | None
    team: str | None
    peer_role: str | None
    minutes: int
    score: float  # 100 = exact name; 97 surname; 95 initial + surname; lower = fuzzy
    exact: bool


class SearchOut(_Out):
    query: str
    scope: str
    results: list[SearchHitOut]


class TeamMinutesOut(_Out):
    name: str
    minutes: int
    matches: int


class RoleStartsOut(_Out):
    role: str
    minutes: int  # as a starter
    starts: int


class SixOrEightOut(_Out):
    """Central midfielders only (D-018). A separate block so the UI can hide it."""

    group: Literal["DM", "CM"]
    method: Literal["depth", "partner", "formation"]
    depth_m: float | None  # metres in front of his centre-backs, season average
    depth_starts: int
    partner_gap_m: float | None  # + = his deepest fellow central mid played deeper
    partner_starts: int
    borderline: bool  # within 1 m of the cut
    cut_m: float
    partner_gap_rule_m: float
    min_starts: int  # starts with a heatmap needed for depth to decide


class RatingOut(_Out):
    average: float | None  # mean over rated matches only
    rated_matches: int


class PlayerCoverageOut(_Out):
    matches: int
    without_advanced: int  # advanced metrics have no data for these matches
    without_heatmap: int


class PlayerOut(_Out):
    id: str
    name: str
    short_name: str | None
    country_code: str | None
    image_url: str | None
    provenance: ProvenanceOut
    minutes: int
    matches: int
    starts: int
    teams: list[TeamMinutesOut]
    primary_role: str | None
    role_source: Literal["starts", "listed"] | None
    starts_by_role: list[RoleStartsOut]
    peer_role: str | None
    six_or_eight: SixOrEightOut | None
    rating: RatingOut
    group: GroupOut | None  # None: not ranked (see not_ranked_reason)
    not_ranked_reason: str | None
    coverage: PlayerCoverageOut


class StatLineOut(_Out):
    metric: str
    label: str
    kind: str
    basis: Literal["per90", "rate"]
    model: bool
    direction: Literal["higher", "lower", "none"]
    # successes for a success rate; null = unknown, never 0, and for an averaged rate
    # (rate_scale percent or metres: distribution_accuracy, avg_pass_length) no total exists
    total: float | None
    attempts: float | None  # fractions only: attempts, or what an averaged rate is weighted by
    per90: float | None
    rate: float | None  # success rates; see rate_scale
    rate_scale: Literal["share", "percent", "metres"] | None  # share = 0-1
    minutes: int  # exposure: minutes in matches with data for this metric
    matches: int
    pct: float | None  # 0-100 within `group`; raw (the UI flips direction = lower)
    ranked_among: int | None
    reason: str | None  # why pct is null


class StatsOut(_Out):
    player: PlayerRefOut
    provenance: ProvenanceOut
    minutes: int
    group: GroupOut | None
    stats: list[StatLineOut]
    missing: list[str]  # asked-for metrics he has no line for (e.g. goals for a keeper)


class MatchValueOut(_Out):
    match_id: str
    date: date
    team: str
    opponent: str
    venue: Literal["home", "away"]
    goals_for: int | None
    goals_against: int | None
    minutes: int
    started: bool | None
    value: float | None  # null: no data that match (or an averaged rate: see `rate`)
    attempts: float | None  # fractions only
    rate: float | None  # fractions only: the match's rate, in the response's rate_scale


class PlayerMatchesOut(_Out):
    player: PlayerRefOut
    provenance: ProvenanceOut
    metric: str
    stored_metric: str  # differs for a volume metric: its parent's per-match values
    basis: Literal["per90", "rate"]
    rate_scale: Literal["share", "percent", "metres"] | None
    matches: list[MatchValueOut]


# --- templates applied, peer groups (slice 3c) ---------------------------------------------------


class TemplateRefOut(_Out):
    id: str
    label: str
    peer_role: str
    # False: a template for another position; his percentiles are still against his own
    # group (`group`), so label them that way. Ranking against another group: /compare.
    same_group: bool


class TemplateSectionViewOut(_Out):
    name: str
    rows: list[StatLineOut]  # the template's order; a metric with no data has minutes 0


class PlayerTemplateOut(_Out):
    """Radar data: one player through one template."""

    player: PlayerRefOut
    provenance: ProvenanceOut
    minutes: int
    group: GroupOut | None  # None: not ranked; every row then has a reason
    template: TemplateRefOut
    sections: list[TemplateSectionViewOut]


class GroupMetricOut(_Out):
    metric: str
    label: str
    basis: Literal["per90", "rate"]  # what `value` is
    rate_scale: Literal["share", "percent", "metres"] | None
    model: bool
    direction: Literal["higher", "lower", "none"]
    ranked_among: int  # members with a percentile for it (eligibility differs per metric)


class GroupValueOut(_Out):
    value: float | None  # per 90 or rate (see the metric's basis); null = no data
    total: float | None  # successes for a success rate; null for averaged rates
    attempts: float | None  # fractions only
    minutes: int  # exposure minutes for this metric (0 = no data)
    pct: float | None  # 0-100 within the group, raw (the UI flips direction = lower)
    reason: str | None  # why pct is null


class GroupPlayerOut(_Out):
    id: str
    name: str
    team: str
    minutes: int
    values: dict[str, GroupValueOut]  # one per requested metric code


class GroupViewOut(_Out):
    """Every member of a peer group on the chosen metrics: rankings, bars, scatter."""

    provenance: ProvenanceOut
    group: GroupOut
    metrics: list[GroupMetricOut]
    sort: str  # the metric the players are ordered by, best first (direction respected)
    players: list[GroupPlayerOut]


# --- compare (slice 3d) ------------------------------------------------------------------------


class CompareMetricOut(_Out):
    metric: str
    label: str
    basis: Literal["per90", "rate"]  # what `value` is
    rate_scale: Literal["share", "percent", "metres"] | None
    model: bool
    direction: Literal["higher", "lower", "none"]


class CompareValueOut(_Out):
    value: float | None  # per 90 or rate; null = no data
    total: float | None  # null for averaged rates
    attempts: float | None  # fractions only
    minutes: int  # exposure minutes for this metric (0 = no data)
    pct: float | None  # 0-100 in the player's `group`, raw (the UI flips direction = lower)
    ranked_among: int | None  # group members ranked on it (an outside player isn't counted)
    reason: str | None  # why pct is null


class ComparePlayerOut(_Out):
    id: str
    name: str
    team: str | None
    minutes: int
    peer_role: str | None  # his own group's role
    group: GroupOut | None  # the group his percentiles are against; None: not ranked
    # True: he isn't a member of `group` (another role, or under its minutes). He's ranked
    # against its members without being added, so nobody else's percentile moves.
    outside_group: bool
    values: dict[str, CompareValueOut]  # one per metric code


class CompareTemplateOut(_Out):
    id: str
    label: str
    peer_role: str


class CompareOut(_Out):
    """2-6 players side by side, each in his own group or all against one (`against`)."""

    provenance: ProvenanceOut
    template: CompareTemplateOut | None  # where the metrics came from; None: `metrics=` given
    against: str | None  # the role everyone is ranked against; None: each in his own group
    min_minutes: int  # the groups' threshold
    stored: bool  # False: a non-default threshold (computed on the fly)
    metrics: list[CompareMetricOut]
    players: list[ComparePlayerOut]
