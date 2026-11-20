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

# How a stored NULL value counts in season totals (Phase 2):
# - "skip": unknown. The match adds nothing to the total or to the metric's minutes.
# - "zero": a known zero the provider sends as null (xGOT with no shot on target). The
#   match adds 0 to the total and its minutes count.
SeasonNull = Literal["skip", "zero"]


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
    # Advanced-endpoint fractions come as two fields (e.g. take_ons_won of take_ons):
    # `source_key` is the successes and `denominator_key` the attempts.
    denominator_key: str | None = None
    # Per-match rates and averages (e.g. distribution_accuracy) can't be added up over a
    # season. With value_is_rate, the stored value is rate x weight (`denominator_key`),
    # so rate_from_sums gives the exact weighted average later.
    value_is_rate: bool = False
    season_null: SeasonNull = "skip"

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
        return self.kind in ("count", "model", "distance") or (
            self.is_fraction and not self.value_is_rate
        )


_P: Final = "pitchapi_players"
_A: Final = "pitchapi_advanced"


def _count(
    code: str,
    label: str,
    source: Source,
    key: str,
    applies: AppliesTo,
    desc: str,
    *,
    lower_is_better: bool = False,
    absence: AbsencePolicy = "zero",
) -> MetricDef:
    return MetricDef(
        code,
        label,
        "count",
        source,
        key,
        absence,
        applies,
        desc,
        higher_is_better=not lower_is_better,
    )


def _model(
    code: str,
    label: str,
    source: Source,
    key: str,
    applies: AppliesTo,
    desc: str,
    unit: str,
    *,
    absence: AbsencePolicy = "zero",
    lower_is_better: bool = False,
    season_null: SeasonNull = "skip",
) -> MetricDef:
    return MetricDef(
        code,
        label,
        "model",
        source,
        key,
        absence,
        applies,
        desc,
        unit=unit,
        higher_is_better=not lower_is_better,
        season_null=season_null,
    )


def _fraction(
    code: str,
    label: str,
    source: Source,
    key: str,
    applies: AppliesTo,
    desc: str,
    *,
    denominator_key: str | None = None,
) -> MetricDef:
    return MetricDef(
        code,
        label,
        "fraction",
        source,
        key,
        "fraction_zero_attempts",
        applies,
        desc,
        denominator_key=denominator_key,
    )


def _distance(code: str, label: str, key: str, desc: str) -> MetricDef:
    return MetricDef(code, label, "distance", _A, key, "zero", "outfield", desc, unit="m")


def _gk_rate(code: str, label: str, key: str, weight_key: str, desc: str, unit: str) -> MetricDef:
    return MetricDef(
        code,
        label,
        "fraction",
        _A,
        key,
        "fraction_zero_attempts",
        "gk",
        desc,
        unit=unit,
        denominator_key=weight_key,
        value_is_rate=True,
    )


# Every source key below was seen in the Phase 0 spike (PITCHAPI_NOTES). v0 (slice 1c) had
# the first 18; slice 1d brings the registry to 90. Codes are stable: never rename or reuse.
REGISTRY: Final[tuple[MetricDef, ...]] = (
    # --- v0 (slice 1c) ---------------------------------------------------------------------
    _count("goals", "Goals", _P, "goals", "outfield", "Goals scored."),
    _count("assists", "Assists", _P, "assists", "outfield", "Assists."),
    _count("shots", "Shots", _P, "total_shots", "outfield",
           "Total shots. Key omitted when the player didn't shoot."),
    _count("shots_on_target", "Shots on target", _P, "ShotsOnTarget", "outfield",
           "Shots on target. Present only when the player shot."),
    _model("xg", "xG", _P, "expected_goals", "outfield",
           "Expected goals (PitchAPI's model).", "xG"),
    _model("npxg", "Non-penalty xG", _P, "expected_goals_non_penalty", "outfield",
           "Expected goals excluding penalties (PitchAPI's model).", "xG"),
    _model("xa", "xA", _P, "expected_assists", "outfield",
           "Expected assists from /players (PitchAPI's model). See also xag.", "xA"),
    _model("xgot", "xGOT", _P, "expected_goals_on_target_variant", "outfield",
           "Expected goals on target. NULL when the player had no shot on target, which "
           "counts as 0 in season totals.", "xG",
           absence="null", season_null="zero"),
    _fraction("passes_completed", "Passes completed", _P, "accurate_passes", "all",
              "Accurate passes out of attempted passes."),
    _fraction("crosses_completed", "Crosses completed", _P, "accurate_crosses", "outfield",
              "Accurate crosses out of attempted crosses."),
    _fraction("dribbles_completed", "Dribbles completed", _P, "dribbles_succeeded", "outfield",
              "Successful dribbles out of attempted dribbles."),
    _count("tackles", "Tackles", _P, "matchstats.headers.tackles", "all",
           "Tackles. Same value as the advanced endpoint's defending.tackles (spike: 277/277)."),
    _count("was_fouled", "Fouls won", _P, "was_fouled", "outfield",
           "Times fouled. The provider sends null instead of 0 for many rows, kept as NULL.",
           absence="null"),
    _count("key_passes", "Key passes", _A, "passing.key_passes", "outfield",
           "Passes leading to a shot (advanced). Equal to the advanced chances_created."),
    _count("progressive_passes", "Progressive passes", _A, "passing.progressive_passes", "all",
           "Passes that move the ball significantly towards goal."),
    _count("progressive_carries", "Progressive carries", _A, "carrying.progressive_carries",
           "outfield", "Carries that move the ball significantly towards goal. Carries are "
           "synthesized by PitchAPI from gaps between actions."),
    _model("xt", "xT", _A, "possession_value.xt_total", "all",
           "Expected threat added (PitchAPI's model).", "xT"),
    _count("saves", "Saves", _P, "saves", "gk", "Saves by the goalkeeper."),
    # --- attacking (1d) ----------------------------------------------------------------------
    _count("shots_off_target", "Shots off target", _P, "ShotsOffTarget", "outfield",
           "Shots off target. Present only when the player shot."),
    _count("shots_blocked", "Shots blocked", _P, "blocked_shots", "outfield",
           "This player's shots that were blocked.", lower_is_better=True),
    _count("shots_woodwork", "Shots hitting the woodwork", _P, "shots_woodwork", "outfield",
           "Shots that hit the post or bar."),
    _count("big_chances_missed", "Big chances missed", _P, "big_chance_missed_title",
           "outfield", "Big chances missed.", lower_is_better=True),
    _count("offsides", "Offsides", _P, "Offsides", "outfield", "Times caught offside.",
           lower_is_better=True),
    _count("touches", "Touches", _P, "touches", "outfield", "Touches of the ball."),
    _count("touches_opp_box", "Touches in opposition box", _P, "touches_opp_box", "outfield",
           "Touches in the opposition penalty area."),
    _count("dispossessed", "Dispossessed", _P, "dispossessed", "outfield",
           "Times dispossessed. Same value as advanced carrying.dispossessed (spike: 277/277).",
           lower_is_better=True),
    # --- creating and passing (1d) -----------------------------------------------------------
    _count("chances_created", "Chances created (/players)", _P, "chances_created", "outfield",
           "/players' chances created. Differs from key_passes in about 7% of rows (spike); "
           "an alternative definition. Never add it to key_passes."),
    _count("big_chances_created", "Big chances created", _P, "big_chance_created_team_title",
           "outfield", "Big chances created."),
    _fraction("long_balls_completed", "Long balls completed", _P, "long_balls_accurate", "all",
              "Accurate long balls out of attempted long balls."),
    _count("passes_final_third", "Passes into final third", _P, "passes_into_final_third",
           "outfield", "Passes into the final third."),
    _count("corners", "Corners taken", _P, "corners", "outfield", "Corners taken."),
    _model("xag", "xAG", _A, "creation.xag", "outfield",
           "Expected assisted goals from the advanced endpoint (PitchAPI's model). How it "
           "differs from xa isn't documented; compare before choosing one.", "xA"),
    _count("sca", "Shot-creating actions", _A, "creation.sca", "outfield",
           "Actions leading to a shot. Player SCA sums short of the team total (foul_drawn "
           "is team-only)."),
    _count("gca", "Goal-creating actions", _A, "creation.gca", "outfield",
           "Actions leading to a goal."),
    _count("sca_pass_live", "SCA: open-play passes", _A, "creation.sca_breakdown.pass_live",
           "outfield", "Shot-creating actions from open-play passes. Missing key = 0 (spike: "
           "the breakdown sums to sca in 277/277 rows)."),
    _count("sca_pass_dead", "SCA: set-piece passes", _A, "creation.sca_breakdown.pass_dead",
           "outfield", "Shot-creating actions from dead-ball passes. Missing key = 0."),
    _count("sca_take_on", "SCA: take-ons", _A, "creation.sca_breakdown.take_on", "outfield",
           "Shot-creating actions from take-ons. Missing key = 0."),
    _count("sca_shot", "SCA: shots", _A, "creation.sca_breakdown.shot", "outfield",
           "Shot-creating actions from a previous shot. Missing key = 0."),
    _count("sca_defensive", "SCA: defensive actions", _A, "creation.sca_breakdown.defensive",
           "outfield", "Shot-creating actions from defensive actions. Missing key = 0."),
    _count("second_assists", "Second assists", _A, "creation.second_assists", "outfield",
           "The pass before the assist."),
    _model("xg_chain", "xG chain", _A, "creation.xg_chain", "outfield",
           "xG of every possession the player took part in (built on shot xG).", "xG"),
    _model("xg_buildup", "xG buildup", _A, "creation.xg_buildup", "outfield",
           "xG chain excluding shots and key passes (built on shot xG).", "xG"),
    _distance("progressive_pass_distance", "Progressive pass distance",
              "passing.progressive_pass_distance",
              "Distance passes moved the ball towards goal (PitchAPI pitch units, metres)."),
    _count("through_balls", "Through balls", _A, "passing.through_balls", "outfield",
           "Through balls."),
    _count("switches", "Switches of play", _A, "passing.switches", "outfield",
           "Long passes switching the side of play."),
    _count("passes_into_box", "Passes into the box", _A, "passing.passes_into_box", "outfield",
           "Passes into the penalty area."),
    # --- carrying and dribbling (1d) ---------------------------------------------------------
    _count("carries", "Carries", _A, "carrying.carries", "outfield",
           "Carries (synthesized by PitchAPI from gaps between actions)."),
    _distance("carry_distance", "Carry distance", "carrying.carry_distance",
              "Total distance carried (PitchAPI pitch units, metres)."),
    _distance("progressive_carry_distance", "Progressive carry distance",
              "carrying.progressive_carry_distance",
              "Distance carries moved the ball towards goal (metres)."),
    _count("carries_final_third", "Carries into final third", _A,
           "carrying.carries_into_final_third", "outfield", "Carries into the final third."),
    _count("carries_into_box", "Carries into the box", _A, "carrying.carries_into_box",
           "outfield", "Carries into the penalty area."),
    _fraction("take_ons_completed", "Take-ons completed", _A, "carrying.take_ons_won",
              "outfield", "Successful take-ons out of attempted take-ons.",
              denominator_key="carrying.take_ons"),
    _count("miscontrols", "Miscontrols", _A, "carrying.miscontrols", "outfield",
           "Miscontrols.", lower_is_better=True),
    # --- defending and duels (1d) ------------------------------------------------------------
    _count("interceptions", "Interceptions", _P, "interceptions", "outfield",
           "Interceptions from /players. The advanced version is higher in ~36% of rows "
           "(spike); never mix the two."),
    _count("clearances", "Clearances", _P, "clearances", "outfield",
           "Clearances from /players (the advanced version differs in ~8% of rows)."),
    _count("headed_clearances", "Headed clearances", _P, "headed_clearance", "outfield",
           "Headed clearances."),
    _count("shot_blocks", "Shots blocked (defending)", _P, "shot_blocks", "outfield",
           "Opponents' shots blocked."),
    _count("recoveries", "Recoveries", _P, "recoveries", "outfield", "Ball recoveries."),
    _count("dribbled_past", "Dribbled past", _P, "dribbled_past", "outfield",
           "Times dribbled past.", lower_is_better=True),
    _count("clearances_off_line", "Clearances off the line", _P, "clearance_off_the_line",
           "outfield", "Clearances off the goal line."),
    _count("last_man_tackles", "Last-man tackles", _P, "last_man_tackle", "outfield",
           "Tackles as the last defender."),
    _count("errors_led_to_goal", "Errors leading to a goal", _P, "errors_led_to_goal", "all",
           "Errors leading to a goal.", lower_is_better=True),
    _count("defensive_actions", "Defensive actions", _P, "defensive_actions", "outfield",
           "PitchAPI's composite of defensive actions (undocumented mix; overlaps tackles, "
           "interceptions and clearances). Don't add it to them."),
    _count("duels_won", "Duels won", _P, "duel_won", "outfield", "Duels won."),
    _count("duels_lost", "Duels lost", _P, "duel_lost", "outfield", "Duels lost.",
           lower_is_better=True),
    _fraction("ground_duels_won", "Ground duels won", _P, "ground_duels_won", "outfield",
              "Ground duels won out of ground duels contested."),
    _fraction("aerials_won", "Aerial duels won", _P, "aerials_won", "outfield",
              "Aerial duels won out of aerial duels contested."),
    _count("fouls_committed", "Fouls committed", _P, "fouls", "all", "Fouls committed.",
           lower_is_better=True),
    # --- penalties and own goals (after the EPL crawl) ---------------------------------------
    # Not in the spike's match; /players sends them only in matches where they happened
    # (checked with scripts/check_new_keys.py), so a missing key means 0.
    _count("penalties_won", "Penalties won", _P, "penalties_won", "outfield",
           "Fouls suffered that gave a penalty."),
    _count("penalties_conceded", "Penalties conceded", _P, "conceded_penalties", "all",
           "Fouls committed that gave away a penalty.", lower_is_better=True),
    _count("penalties_missed", "Penalties missed", _P, "missed_penalty", "outfield",
           "Penalties taken and not scored.", lower_is_better=True),
    _count("own_goals", "Own goals", _P, "owngoal", "all", "Own goals.",
           lower_is_better=True),
    _count("penalties_saved", "Penalties saved", _P, "saved_penalties", "gk",
           "Penalties saved by the goalkeeper."),
    # --- possession value (1d), all model outputs --------------------------------------------
    _model("vaep", "VAEP", _A, "possession_value.vaep_total", "all",
           "Valuing actions by estimating probabilities (PitchAPI's model).", "VAEP"),
    _model("vaep_offensive", "VAEP (offensive)", _A, "possession_value.vaep_offensive", "all",
           "Offensive part of VAEP.", "VAEP"),
    _model("vaep_defensive", "VAEP (defensive)", _A, "possession_value.vaep_defensive", "all",
           "Defensive part of VAEP.", "VAEP"),
    _model("pv", "Possession value", _A, "possession_value.pv_total", "all",
           "Possession value added (PitchAPI's model).", "PV"),
    _model("pv_offensive", "Possession value (offensive)", _A,
           "possession_value.pv_offensive", "all", "Offensive part of PV.", "PV"),
    _model("pv_defensive", "Possession value (defensive)", _A,
           "possession_value.pv_defensive", "all", "Defensive part of PV.", "PV"),
    # --- goalkeeping (1d) --------------------------------------------------------------------
    _count("saves_inside_box", "Saves inside the box", _P, "saves_inside_box", "gk",
           "Saves from shots inside the box."),
    _count("goals_conceded", "Goals conceded", _P, "goals_conceded", "gk",
           "Goals conceded while on the pitch.", lower_is_better=True),
    _model("goals_prevented", "Goals prevented", _P, "goals_prevented", "gk",
           "xGOT faced minus goals conceded (PitchAPI's model).", "xG"),
    _model("xgot_faced", "xGOT faced", _P, "expected_goals_on_target_faced", "gk",
           "Expected goals on target faced (PitchAPI's model).", "xG", lower_is_better=True),
    _count("diving_saves", "Diving saves", _P, "keeper_diving_save", "gk", "Diving saves."),
    _count("high_claims", "High claims", _P, "keeper_high_claim", "gk", "High claims."),
    _count("sweeper_actions", "Sweeper actions", _P, "keeper_sweeper", "gk",
           "Actions as a sweeper keeper (from /players, so sub keepers are covered too)."),
    _count("punches", "Punches", _P, "punches", "gk", "Punches."),
    _count("throws", "Throws", _P, "player_throws", "gk", "Throws."),
    _fraction("claims_won", "Claims won", _A, "goalkeeping.claims_won", "gk",
              "Claims won out of claims attempted. Starting keepers only (the advanced "
              "goalkeeping group is null for sub keepers).",
              denominator_key="goalkeeping.claims"),
    _count("distributions", "Distributions", _A, "goalkeeping.distributions", "gk",
           "Distributions. Starting keepers only."),
    _count("launches", "Launches", _A, "goalkeeping.launches", "gk",
           "Long launched distributions. Starting keepers only."),
    _gk_rate("distribution_accuracy", "Distribution accuracy", "goalkeeping.distribution_accuracy",
             "goalkeeping.distributions",
             "Per-match accuracy, stored weighted by distributions. Starting keepers only. "
             "Same scale as PitchAPI sends it.", "rate"),
    _gk_rate("launch_pct", "Launch share", "goalkeeping.launch_pct", "goalkeeping.distributions",
             "Per-match share of launched distributions, weighted by distributions. Equals "
             "launches / distributions if that's how PitchAPI defines it; check in Phase 2.",
             "rate"),
    _gk_rate("claim_rate", "Claim rate", "goalkeeping.claim_rate", "goalkeeping.claims",
             "Per-match claim success rate, weighted by claims. NULL rate with claims = 0 "
             "contributes nothing. Same information as claims_won.", "rate"),
    _gk_rate("avg_pass_length", "Average pass length", "goalkeeping.avg_pass_length",
             "goalkeeping.distributions",
             "Per-match average pass length, weighted by distributions (an approximation: "
             "which passes it averages isn't documented).", "m"),
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
        # Seen in the EPL 2025/26 crawl, not in the spike's match:
        "conceded_penalties", "missed_penalty", "owngoal", "penalties_won", "saved_penalties",
    }
)  # fmt: skip

# Keys only goalkeepers have; the GK fallback when lineup data is missing.
KEEPER_ONLY_KEYS: Final = frozenset(
    {
        "saves", "saves_inside_box", "goals_conceded", "goals_prevented", "saved_penalties",
        "expected_goals_on_target_faced",
        "keeper_diving_save", "keeper_high_claim", "keeper_sweeper", "punches", "player_throws",
    }
)  # fmt: skip
