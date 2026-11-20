"""Pydantic models for PitchAPI `data` payloads (validated at the boundary).

Shapes come from the Phase 0 spike (PITCHAPI_NOTES.md), not from guesses. Fields we use
are required, so a missing or mistyped one fails loudly. Fields we don't use are
ignored, so PitchAPI adding a field doesn't break the sync.

IDs are opaque: we only check the prefix, so an ID of the wrong kind (a team ID where a
match ID belongs) is caught, without assuming anything about the rest of the string.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, RootModel


def _prefixed(prefix: str) -> AfterValidator:
    def check(value: str) -> str:
        if not value.startswith(prefix) or len(value) <= len(prefix):
            raise ValueError(f"expected an ID starting with {prefix!r}, got {value!r}")
        return value

    return AfterValidator(check)


LeagueId = Annotated[str, _prefixed("l_")]
TeamId = Annotated[str, _prefixed("t_")]
MatchId = Annotated[str, _prefixed("m_")]
NonEmpty = Annotated[str, Field(min_length=1)]


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


# --- GET /v1/leagues --------------------------------------------------------------------------


class League(_Payload):
    id: LeagueId
    name: NonEmpty
    country_code: NonEmpty
    image_url: str | None = None
    seasons: list[NonEmpty] = Field(min_length=1)

    @property
    def latest_season(self) -> str:
        """The current season. Labels sort correctly as text ("2025/2026" < "2026/2027",
        "2025" < "2026"), so this doesn't depend on the order PitchAPI lists them in."""
        return max(self.seasons)


class LeaguesPayload(_Payload):
    leagues: list[League]


# --- GET /v1/leagues/{id}/matches -------------------------------------------------------------


class TeamRef(_Payload):
    id: TeamId
    name: NonEmpty
    image_url: str | None = None


class MatchListLeague(_Payload):
    id: LeagueId
    name: NonEmpty
    season: NonEmpty
    image_url: str | None = None


class MatchRow(_Payload):
    id: MatchId
    # Named match_date (JSON key "date") so the field doesn't shadow the `date` type.
    match_date: date = Field(alias="date")
    time_utc: AwareDatetime
    status: NonEmpty
    home_team: TeamRef
    away_team: TeamRef
    score_home: int | None = None
    score_away: int | None = None


class MatchListPayload(_Payload):
    league: MatchListLeague
    matches: list[MatchRow]


# --- GET /v1/matches/{id}/players -------------------------------------------------------------
#
# Spike-verified shape (PITCHAPI_NOTES "Player stat lines"): `data` is a bare list of rows;
# each row has `stats`, a list of groups; each group's `stats` is an object keyed by display
# label whose values carry the stable inner `key` and a nested `stat`. We read by inner key,
# never by label.

PlayerId = Annotated[str, _prefixed("p_")]

# Value types the docs list. Anything else fails loudly so a format change gets noticed.
KNOWN_STAT_TYPES = frozenset(
    {
        "integer",
        "double",
        "fractionWithPercentage",
        "fantasyPoints",
        "boolean",
        "distance",
        "distanceWithPercentage",
        "speed",
    }
)


class StatValue(_Payload):
    type: NonEmpty
    # Any: physical-metric types haven't been seen yet, so their value shape is unverified.
    # Registry metrics check that their values are numbers when they read them.
    value: Any = None
    total: Any = None


class StatEntry(_Payload):
    key: str  # may be "" (e.g. the "Shotmap" boolean); such entries are skipped
    stat: StatValue


class StatGroup(_Payload):
    key: NonEmpty
    title: str | None = None
    stats: dict[str, StatEntry] = Field(default_factory=dict)


class PlayerRef(_Payload):
    id: PlayerId
    name: NonEmpty
    image_url: str | None = None


class PlayerStatRow(_Payload):
    player: PlayerRef
    team_id: TeamId
    stats: list[StatGroup] = Field(default_factory=list)

    def flat_stats(self) -> dict[str, StatValue]:
        """Inner key → stat, across all groups. Booleans and empty keys are skipped.

        Raises ValueError on an unknown stat type, or when a key that appears in two
        groups (14 keys do, per the spike) has conflicting values.
        """
        out: dict[str, StatValue] = {}
        for group in self.stats:
            for entry in group.stats.values():
                if entry.stat.type not in KNOWN_STAT_TYPES:
                    raise ValueError(
                        f"{self.player.id}: unknown stat type {entry.stat.type!r} "
                        f"for key {entry.key!r}"
                    )
                if not entry.key or entry.stat.type == "boolean":
                    continue
                previous = out.get(entry.key)
                if previous is not None and previous != entry.stat:
                    raise ValueError(
                        f"{self.player.id}: key {entry.key!r} has conflicting values "
                        f"across groups: {previous} vs {entry.stat}"
                    )
                out[entry.key] = entry.stat
        return out


class PlayersPayload(RootModel[list[PlayerStatRow]]):
    model_config = ConfigDict(frozen=True)


# --- GET /v1/matches/{id}/advanced/players ----------------------------------------------------


class AdvancedPlayerRef(_Payload):
    id: PlayerId
    name: NonEmpty


class AdvancedRow(_Payload):
    player: AdvancedPlayerRef
    team_id: TeamId
    minutes_played: int = Field(ge=0)
    possession_value: dict[str, Any] | None = None
    passing: dict[str, Any] | None = None
    carrying: dict[str, Any] | None = None
    creation: dict[str, Any] | None = None
    defending: dict[str, Any] | None = None
    goalkeeping: dict[str, Any] | None = None  # null for everyone but the starting keepers

    def lookup(self, dotted: str) -> tuple[bool, Any]:
        """(found, value) for a path like "passing.key_passes". A null group counts as
        not found; a present key with a null value is (True, None)."""
        node: Any = self.model_dump()
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return False, None
            node = node[part]
        return True, node


class AdvancedPayload(_Payload):
    players: list[AdvancedRow]


# --- GET /v1/matches/{id}/lineups -------------------------------------------------------------


class LineupEntry(_Payload):
    player_id: PlayerId
    name: str | None = None
    # Starters: formation grid code (e.g. 38). Subs: broad category (0 GK, 1 DEF, 2 MID,
    # 3 ATT, -1 unclassified). See PITCHAPI_NOTES "Positions".
    position_id: int
    pitch_x: float | None = None
    pitch_y: float | None = None


class LineupSide(_Payload):
    formation: str | None = None
    starters: list[LineupEntry] = Field(default_factory=list)
    subs: list[LineupEntry] = Field(default_factory=list)


class LineupsPayload(_Payload):
    home: LineupSide
    away: LineupSide
