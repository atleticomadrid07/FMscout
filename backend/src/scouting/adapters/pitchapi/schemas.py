"""Pydantic models for PitchAPI `data` payloads (validated at the boundary).

Shapes come from the Phase 0 spike (PITCHAPI_NOTES.md), not from guesses. Fields we use
are required, so a missing or mistyped one fails loudly. Fields we don't use are
ignored, so PitchAPI adding a field doesn't break the sync.

IDs are opaque: we only check the prefix, so an ID of the wrong kind (a team ID where a
match ID belongs) is caught, without assuming anything about the rest of the string.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field


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


# --- GET /v1/leagues ---------------------------------------------------------------------------


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


# --- GET /v1/leagues/{id}/matches ----------------------------------------------------------------


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
