"""Metric registry: the single source of truth for which metrics exist (SCHEMA.md §5).

The `metric` table is synced from this module on every sync; codes are stable and
never reused. Only metrics listed here are ingested. Everything else stays in raw.db.

Absence policies (PITCHAPI_NOTES, "What a missing key or null means"):
- "zero": key missing for a played row → 0 (omitted-when-zero keys)
- "null": key missing → NULL; a provider null stays NULL
- "fraction_zero_attempts": fraction key missing → 0 successes of 0 attempts (rate NULL)

`applies_to` keeps metrics off players they don't describe: a striker gets no `saves`
row and a keeper gets no `shots` row (no row at all, not a 0).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

Source = Literal["pitchapi_players", "pitchapi_advanced", "fm"]
Kind = Literal["count", "fraction", "model", "rating", "distance"]
AbsencePolicy = Literal["zero", "null", "fraction_zero_attempts"]
Aggregation = Literal["sum", "rate_from_sums"]
AppliesTo = Literal["all", "outfield", "gk"]

SOURCES: Final[tuple[Source, ...]] = ("pitchapi_players", "pitchapi_advanced", "fm")
KINDS: Final[tuple[Kind, ...]] = ("count", "fraction", "model", "rating", "distance")
ABSENCE_POLICIES: Final[tuple[AbsencePolicy, ...]] = ("zero", "null", "fraction_zero_attempts")
AGGREGATIONS: Final[tuple[Aggregation, ...]] = ("sum", "rate_from_sums")
APPLIES_TO: Final[tuple[AppliesTo, ...]] = ("all", "outfield", "gk")


@dataclass(frozen=True, slots=True)
class MetricDef:
    code: str
    label: str
    kind: Kind
    source: Source
    source_key: str  # /players inner key, or a dotted path into an advanced row
    absence_policy: AbsencePolicy
    applies_to: AppliesTo
    description: str
    unit: str = "count"
    higher_is_better: bool = True

    @property
    def is_fraction(self) -> bool:
        return self.kind == "fraction"

    @property
    def is_model_output(self) -> bool:
        return self.kind == "model"

    @property
    def aggregation(self) -> Aggregation:
        return "rate_from_sums" if self.is_fraction else "sum"

    @property
    def per90_allowed(self) -> bool:
        return self.kind in ("count", "model", "distance") or self.is_fraction


_P: Final = "pitchapi_players"
_A: Final = "pitchapi_advanced"

# v0: 18 metrics chosen to exercise every absence policy and every `applies_to` value.
# Phase 1d grows this to the full list (~80).
REGISTRY: Final[tuple[MetricDef, ...]] = (
    MetricDef("goals", "Goals", "count", _P, "goals", "zero", "outfield", "Goals scored."),
    MetricDef("assists", "Assists", "count", _P, "assists", "zero", "outfield", "Assists."),
    MetricDef(
        "shots", "Shots", "count", _P, "total_shots", "zero", "outfield",
        "Total shots. Key omitted when the player didn't shoot.",
    ),
    MetricDef(
        "shots_on_target", "Shots on target", "count", _P, "ShotsOnTarget", "zero", "outfield",
        "Shots on target. Present only when the player shot.",
    ),
    MetricDef(
        "xg", "xG", "model", _P, "expected_goals", "zero", "outfield",
        "Expected goals (PitchAPI's model).", unit="xG",
    ),
    MetricDef(
        "npxg", "Non-penalty xG", "model", _P, "expected_goals_non_penalty", "zero", "outfield",
        "Expected goals excluding penalties (PitchAPI's model).", unit="xG",
    ),
    MetricDef(
        "xa", "xA", "model", _P, "expected_assists", "zero", "outfield",
        "Expected assists (PitchAPI's model).", unit="xA",
    ),
    MetricDef(
        "xgot", "xGOT", "model", _P, "expected_goals_on_target_variant", "null", "outfield",
        "Expected goals on target. NULL when the player had no shot on target.", unit="xG",
    ),
    MetricDef(
        "passes_completed", "Passes completed", "fraction", _P, "accurate_passes",
        "fraction_zero_attempts", "all", "Accurate passes out of attempted passes.",
    ),
    MetricDef(
        "crosses_completed", "Crosses completed", "fraction", _P, "accurate_crosses",
        "fraction_zero_attempts", "outfield", "Accurate crosses out of attempted crosses.",
    ),
    MetricDef(
        "dribbles_completed", "Dribbles completed", "fraction", _P, "dribbles_succeeded",
        "fraction_zero_attempts", "outfield", "Successful dribbles out of attempted dribbles.",
    ),
    MetricDef(
        "tackles", "Tackles", "count", _P, "matchstats.headers.tackles", "zero", "all",
        "Tackles. Same value as the advanced endpoint's defending.tackles (spike: 277/277).",
    ),
    MetricDef(
        "was_fouled", "Fouls won", "count", _P, "was_fouled", "null", "outfield",
        "Times fouled. The provider sends null instead of 0 for many rows, kept as NULL.",
    ),
    MetricDef(
        "key_passes", "Key passes", "count", _A, "passing.key_passes", "zero", "outfield",
        "Passes leading to a shot. Equal to chances_created (spike: 277/277).",
    ),
    MetricDef(
        "progressive_passes", "Progressive passes", "count", _A, "passing.progressive_passes",
        "zero", "all", "Passes that move the ball significantly towards goal.",
    ),
    MetricDef(
        "progressive_carries", "Progressive carries", "count", _A,
        "carrying.progressive_carries", "zero", "outfield",
        "Carries that move the ball significantly towards goal. Carries are synthesized by "
        "PitchAPI from gaps between actions.",
    ),
    MetricDef(
        "xt", "xT", "model", _A, "possession_value.xt_total", "zero", "all",
        "Expected threat added (PitchAPI's model).", unit="xT",
    ),
    MetricDef("saves", "Saves", "count", _P, "saves", "zero", "gk", "Saves by the goalkeeper."),
)  # fmt: skip

BY_CODE: Final[dict[str, MetricDef]] = {m.code: m for m in REGISTRY}

# Every /players key seen in the Phase 0 spike (PITCHAPI_NOTES). A key outside this set and
# outside the registry is new from the provider; the sync logs it once so it gets noticed.
KNOWN_PLAYER_KEYS: Final = frozenset(
    {
        "Offsides", "ShotsOffTarget", "ShotsOnTarget", "accurate_crosses", "accurate_passes",
        "aerials_won", "assists", "big_chance_created_team_title", "big_chance_missed_title",
        "blocked_shots", "chances_created", "clearance_off_the_line", "clearances", "corners",
        "defensive_actions", "dispossessed", "dribbled_past", "dribbles_succeeded", "duel_lost",
        "duel_won", "errors_led_to_goal", "expected_assists", "expected_goals",
        "expected_goals_non_penalty", "expected_goals_on_target_faced",
        "expected_goals_on_target_variant", "fantasy_points", "fouls", "goals", "goals_conceded",
        "goals_prevented", "ground_duels_won", "headed_clearance", "interceptions",
        "keeper_diving_save", "keeper_high_claim", "keeper_sweeper", "last_man_tackle",
        "long_balls_accurate", "matchstats.headers.tackles", "minutes_played",
        "passes_into_final_third", "player_throws", "punches", "rating_title", "recoveries",
        "saves", "saves_inside_box", "shot_accuracy", "shot_blocks", "shots_woodwork",
        "total_shots", "touches", "touches_opp_box", "was_fouled", "xg_and_xa",
    }
)  # fmt: skip

# Keys only goalkeepers have; the GK fallback when lineup data is missing.
KEEPER_ONLY_KEYS: Final = frozenset(
    {
        "saves", "saves_inside_box", "goals_prevented", "expected_goals_on_target_faced",
        "keeper_diving_save", "keeper_high_claim", "keeper_sweeper", "punches", "player_throws",
    }
)  # fmt: skip
