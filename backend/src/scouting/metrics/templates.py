"""Position templates (slice 2e, D-015, D-019): which metrics a radar shows, in sections.

Three pieces, shared by the built-in templates now and by user-made ones later (Phase 3-4):
- `Template`: one shape for both. Built-ins come from mappings/position_templates.toml.
- `check_template` / `check_coverage`: findings with a severity. Built-in templates must have
  none (the tests fail otherwise); for a custom template the UI shows the warnings and the
  user decides. Errors are templates that can't work at all.
- `apply_template`: a player-season seen through a template, row by row, with the reason
  when there's no percentile. The radar, the comparison view and the API read this.

A template only selects stored values: percentiles already exist for every metric in every
peer group, so applying a template computes nothing new.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scouting.db.queries import CoverageRow, GroupInfo, PlayerSeason, SeasonStatLine
from scouting.domain.positions import PEER_ROLES
from scouting.metrics.registry import BY_CODE, MetricDef

TEMPLATE_FILE: Final = "position_templates.toml"
SIZE_MIN: Final = 8  # decision D (slice 2e): 8-14 metrics, about 12
SIZE_MAX: Final = 14
MIN_RANKED_SHARE: Final = 0.5  # fewer of the group ranked than this: a warning
# Two codes for nearly the same thing: one per template (decision B and D-016).
NEAR_DUPLICATES: Final[tuple[tuple[str, str], ...]] = (
    ("xa", "xag"),
    ("key_passes", "chances_created"),
    ("dribbles_completed", "take_ons_completed"),
    ("claims_won", "claim_rate"),
)

Severity = Literal["error", "warning"]


class TemplateError(Exception):
    """The template file can't be read as templates."""


@dataclass(frozen=True, slots=True)
class TemplateSection:
    name: str
    metrics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Template:
    id: str  # "builtin:ST" for built-ins; user templates get their own ids later
    peer_role: str
    label: str
    sections: tuple[TemplateSection, ...]
    builtin: bool

    @property
    def codes(self) -> list[str]:
        return [code for section in self.sections for code in section.metrics]


@dataclass(frozen=True, slots=True)
class Finding:
    severity: Severity
    rule: str  # unknown_metric, wrong_position, duplicate, empty, unknown_role, outcome,
    # near_duplicate, size, low_coverage
    message: str
    code: str | None = None  # the metric it's about, if any


# --- loading ---------------------------------------------------------------------------------


class _SectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    metrics: list[str]


class _TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1)
    sections: list[_SectionIn]


def parse_templates(text: str, *, builtin: bool) -> dict[str, Template]:
    """Templates keyed by peer role, from TOML text. Shape errors raise TemplateError; what
    the metrics mean is left to check_template."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TemplateError(f"not valid TOML: {exc}") from exc
    templates: dict[str, Template] = {}
    for role, body in raw.items():
        try:
            parsed = _TemplateIn.model_validate(body)
        except ValidationError as exc:
            raise TemplateError(f"template {role}: {exc}") from exc
        templates[role] = Template(
            id=f"builtin:{role}" if builtin else role,
            peer_role=role,
            label=parsed.label,
            sections=tuple(TemplateSection(s.name, tuple(s.metrics)) for s in parsed.sections),
            builtin=builtin,
        )
    return templates


def builtin_templates() -> dict[str, Template]:
    """The built-in templates, one per peer role."""
    text = resources.files("scouting.mappings").joinpath(TEMPLATE_FILE).read_text("utf-8")
    return parse_templates(text, builtin=True)


# --- checks ----------------------------------------------------------------------------------


def _applies(metric: MetricDef, peer_role: str) -> bool:
    allowed = ("all", "gk") if peer_role == "GK" else ("all", "outfield")
    return metric.applies_to in allowed


def check_template(template: Template) -> list[Finding]:
    """What's wrong with a template, from the registry alone. Built-in templates turn the
    judgement-call rules (outcomes, near-duplicates, size) into errors."""
    strict: Severity = "error" if template.builtin else "warning"
    findings: list[Finding] = []
    if template.peer_role not in PEER_ROLES:
        findings.append(
            Finding("error", "unknown_role", f"unknown peer role {template.peer_role!r}")
        )
    if not template.sections:
        findings.append(Finding("error", "empty", "the template has no sections"))
    for section in template.sections:
        if not section.metrics:
            findings.append(Finding("error", "empty", f"section {section.name!r} is empty"))
    seen: set[str] = set()
    for code in template.codes:
        if code in seen:
            findings.append(Finding("error", "duplicate", f"{code} appears twice", code))
            continue
        seen.add(code)
        metric = BY_CODE.get(code)
        if metric is None:
            findings.append(Finding("error", "unknown_metric", f"unknown metric {code}", code))
            continue
        if template.peer_role in PEER_ROLES and not _applies(metric, template.peer_role):
            whose = "goalkeepers only" if metric.applies_to == "gk" else "outfield players only"
            findings.append(
                Finding(
                    "error",
                    "wrong_position",
                    f"{code} is recorded for {whose}: no {template.peer_role} has it",
                    code,
                )
            )
        if metric.outcome:
            findings.append(
                Finding(
                    strict,
                    "outcome",
                    f"{code} is an outcome (needs a goal or a penalty), not a process metric",
                    code,
                )
            )
    for first, second in NEAR_DUPLICATES:
        if first in seen and second in seen:
            findings.append(
                Finding(
                    strict,
                    "near_duplicate",
                    f"{first} and {second} measure nearly the same thing: keep one",
                    second,
                )
            )
    size = len(template.codes)
    if not SIZE_MIN <= size <= SIZE_MAX:
        findings.append(
            Finding(strict, "size", f"{size} metrics (radars read best with {SIZE_MIN}-{SIZE_MAX})")
        )
    return findings


def check_coverage(
    template: Template, groups: list[GroupInfo], rows: list[CoverageRow]
) -> list[Finding]:
    """Warnings from a real season: metrics on which fewer than half of the template's peer
    group is ranked (too few attempts, or no data), so the radar would often show gaps."""
    group = next((g for g in groups if g.peer_role == template.peer_role), None)
    if group is None or group.size == 0:
        return [
            Finding("warning", "low_coverage", f"no {template.peer_role} peer group this season")
        ]
    by_code = {row.code: row for row in rows}
    findings = []
    for code in template.codes:
        row = by_code.get(code)
        ranked = row.cells[group.peer_role].ranked if row is not None else 0
        if ranked < MIN_RANKED_SHARE * group.size:
            findings.append(
                Finding(
                    "warning",
                    "low_coverage",
                    f"{code}: only {ranked} of {group.size} {group.peer_role}s ranked",
                    code,
                )
            )
    return findings


# --- applying a template to a player ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TemplateRow:
    code: str
    label: str
    is_model_output: bool
    direction: str
    basis: str  # "per90" or "rate"
    line: SeasonStatLine | None  # the season numbers; None = no data for this metric
    pct: float | None  # within the player's own peer group
    ranked_among: int | None
    reason: str | None  # why there's no percentile; None when there is one


@dataclass(frozen=True, slots=True)
class TemplateView:
    template: Template
    group: GroupInfo | None  # the peer group his percentiles come from; None = not ranked
    # False: the template is for another position than his peer group, so his percentiles
    # are still against his own group and must be labelled that way (cross-position views
    # are a backlog item).
    same_group: bool
    sections: tuple[tuple[str, tuple[TemplateRow, ...]], ...]


def missing_reason(line: SeasonStatLine | None, season: PlayerSeason) -> str:
    """Why a metric has no percentile for this player."""
    if season.group is None:
        if season.peer_role is None:
            return "no peer role"
        threshold = season.group_min_minutes
        return f"not ranked: below {threshold} min" if threshold is not None else "not ranked"
    if line is None:
        return "no data"
    return stat_reason(
        is_fraction=line.kind == "fraction",
        min_attempts=line.min_attempts,
        attempts=line.denominator,
        minutes=line.minutes,
        group_min_minutes=season.group.min_minutes,
    )


def stat_reason(
    *,
    is_fraction: bool,
    min_attempts: int | None,
    attempts: float | None,
    minutes: int,
    group_min_minutes: int,
) -> str:
    """Why a member of a peer group has no percentile for a metric he has data for
    (the eligibility rules of metrics.percentiles)."""
    if is_fraction:
        if min_attempts is not None and (attempts or 0) < min_attempts:
            return f"needs {min_attempts} attempts (has {int(attempts or 0)})"
        return "no attempts"
    if minutes < group_min_minutes:
        return f"{minutes} min with data (needs {group_min_minutes})"
    return "not ranked"


def apply_template(season: PlayerSeason, template: Template) -> TemplateView:
    """The player's season through a template: one row per metric, in the template's order.
    The template must pass check_template without errors."""
    lines = {line.code: line for line in season.stats}
    sections = []
    for section in template.sections:
        rows = []
        for code in section.metrics:
            metric = BY_CODE.get(code)
            if metric is None:
                raise TemplateError(f"unknown metric {code}: run check_template first")
            line = lines.get(code)
            pct = line.pct if line is not None else None
            rows.append(
                TemplateRow(
                    code=code,
                    label=metric.label,
                    is_model_output=metric.is_model_output,
                    direction=metric.direction,
                    basis="rate" if metric.is_fraction else "per90",
                    line=line,
                    pct=pct,
                    ranked_among=line.ranked_among if line is not None else None,
                    reason=None if pct is not None else missing_reason(line, season),
                )
            )
        sections.append((section.name, tuple(rows)))
    return TemplateView(
        template=template,
        group=season.group,
        same_group=season.peer_role == template.peer_role,
        sections=tuple(sections),
    )
