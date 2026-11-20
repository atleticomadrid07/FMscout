"""Sync one league-season's match list into scouting.db (slice 1b).

Flow: /v1/leagues (cached) → find the league and check the season → match list
(cached; refetched only for the current season) → parse → upsert competition,
season, teams and matches by PitchAPI ID → record sync state.

Idempotency: rows are matched on their PitchAPI IDs through the *_source_id maps and
updated in place, so a second run changes nothing. `sync_state` stores the match
list's `fetched_at` rather than the current time, which keeps a re-run from cache
byte-for-byte identical.

The caller owns the transaction: everything here only adds and flushes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.cache import (
    LEAGUES,
    MATCH_LIST_COMPLETED,
    MATCH_LIST_CURRENT,
    CachedFetcher,
    FetchResult,
)
from scouting.adapters.pitchapi.client import build_path
from scouting.adapters.pitchapi.schemas import (
    League,
    LeaguesPayload,
    MatchListPayload,
    MatchRow,
    TeamRef,
)
from scouting.db.models import (
    Competition,
    CompetitionSourceId,
    Match,
    MatchSourceId,
    Season,
    SyncState,
    Team,
    TeamSourceId,
    to_utc_naive,
)

SOURCE = "pitchapi"
MATCH_LIST_STATUS = "played"  # post-match data only (D-005)

Outcome = Literal["inserted", "updated", "unchanged"]


class SyncError(RuntimeError):
    """The data doesn't fit what we expect; the sync stops without writing anything."""


@dataclass(slots=True)
class UpsertCounts:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    def add(self, outcome: Outcome) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged


@dataclass(slots=True)
class MatchListSummary:
    league_id: str
    competition: str
    season: str
    is_current: bool
    match_list_from_cache: bool = False
    teams: UpsertCounts = field(default_factory=UpsertCounts)
    matches: UpsertCounts = field(default_factory=UpsertCounts)


def _apply(obj: object, **values: object) -> bool:
    """Set attributes that differ; return whether anything changed."""
    changed = False
    for name, value in values.items():
        if getattr(obj, name) != value:
            setattr(obj, name, value)
            changed = True
    return changed


# --- upserts -------------------------------------------------------------------------------------


def upsert_competition(session: Session, league: League) -> tuple[Competition, Outcome]:
    stmt = (
        select(Competition)
        .join(CompetitionSourceId, CompetitionSourceId.competition_id == Competition.id)
        .where(CompetitionSourceId.source == SOURCE, CompetitionSourceId.external_id == league.id)
    )
    competition = session.scalars(stmt).one_or_none()
    values = {
        "name": league.name,
        "country_code": league.country_code,
        "image_url": league.image_url,
    }
    if competition is None:
        competition = Competition(**values)
        session.add(competition)
        session.flush()
        session.add(
            CompetitionSourceId(source=SOURCE, external_id=league.id, competition_id=competition.id)
        )
        return competition, "inserted"
    return competition, "updated" if _apply(competition, **values) else "unchanged"


def upsert_season(session: Session, competition: Competition, league: League, label: str) -> Season:
    season = session.scalars(
        select(Season).where(Season.competition_id == competition.id, Season.label == label)
    ).one_or_none()
    if season is None:
        season = Season(competition_id=competition.id, label=label, is_current=False)
        session.add(season)
        session.flush()
    # Keep is_current right for every stored season of this league, not just this one:
    # once a new season appears, the old one stops being current.
    session.execute(
        update(Season)
        .where(Season.competition_id == competition.id)
        .values(is_current=Season.label == league.latest_season)
    )
    session.refresh(season)
    return season


def upsert_team(session: Session, ref: TeamRef, *, overwrite: bool) -> tuple[Team, Outcome]:
    """`overwrite` is False when syncing an older season: a backfill of 2021/22 must not
    rename a team back to what it was called then."""
    stmt = (
        select(Team)
        .join(TeamSourceId, TeamSourceId.team_id == Team.id)
        .where(TeamSourceId.source == SOURCE, TeamSourceId.external_id == ref.id)
    )
    team = session.scalars(stmt).one_or_none()
    if team is None:
        team = Team(name=ref.name, image_url=ref.image_url)
        session.add(team)
        session.flush()
        session.add(TeamSourceId(source=SOURCE, external_id=ref.id, team_id=team.id))
        return team, "inserted"
    values: dict[str, object] = {}
    if overwrite or not team.name:
        values["name"] = ref.name
    if overwrite or team.image_url is None:
        values["image_url"] = ref.image_url
    return team, "updated" if _apply(team, **values) else "unchanged"


def upsert_match(
    session: Session, row: MatchRow, *, season_id: int, home_team_id: int, away_team_id: int
) -> Outcome:
    stmt = (
        select(Match)
        .join(MatchSourceId, MatchSourceId.match_id == Match.id)
        .where(MatchSourceId.source == SOURCE, MatchSourceId.external_id == row.id)
    )
    match = session.scalars(stmt).one_or_none()
    # Only fields that come from the match list. The per-match statuses, formations and
    # normalized_at belong to the per-match sync (1c) and are left alone here.
    values: dict[str, object] = {
        "season_id": season_id,
        "kickoff_utc": to_utc_naive(row.time_utc),
        "match_date": row.match_date,
        "status": row.status,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "home_score": row.score_home,
        "away_score": row.score_away,
    }
    if match is None:
        match = Match(**values)
        session.add(match)
        session.flush()
        session.add(MatchSourceId(source=SOURCE, external_id=row.id, match_id=match.id))
        return "inserted"
    return "updated" if _apply(match, **values) else "unchanged"


# --- normalize one match list --------------------------------------------------------------------


def normalize_match_list(
    session: Session, league: League, payload: MatchListPayload, *, fetched_at: datetime
) -> MatchListSummary:
    label = payload.league.season
    if payload.league.id != league.id:
        raise SyncError(f"match list is for league {payload.league.id}, expected {league.id}")
    if label not in league.seasons:
        raise SyncError(f"season {label!r} isn't listed for {league.id}: {league.seasons}")
    duplicates = [mid for mid, n in Counter(m.id for m in payload.matches).items() if n > 1]
    if duplicates:
        raise SyncError(f"match list repeats match IDs: {duplicates[:5]}")

    is_current = label == league.latest_season
    summary = MatchListSummary(
        league_id=league.id, competition=league.name, season=label, is_current=is_current
    )

    competition, _ = upsert_competition(session, league)
    season = upsert_season(session, competition, league, label)

    team_ids: dict[str, int] = {}
    for row in payload.matches:
        for ref in (row.home_team, row.away_team):
            if ref.id not in team_ids:
                team, outcome = upsert_team(session, ref, overwrite=is_current)
                team_ids[ref.id] = team.id
                summary.teams.add(outcome)

    for row in payload.matches:
        if row.home_team.id == row.away_team.id:
            raise SyncError(f"match {row.id} has the same team on both sides")
        summary.matches.add(
            upsert_match(
                session,
                row,
                season_id=season.id,
                home_team_id=team_ids[row.home_team.id],
                away_team_id=team_ids[row.away_team.id],
            )
        )

    state = session.get(SyncState, season.id)
    if state is None:
        state = SyncState(season_id=season.id)
        session.add(state)
    state.match_list_fetched_at = to_utc_naive(fetched_at)
    session.flush()
    return summary


# --- fetch + normalize ---------------------------------------------------------------------------


def _parse[T: (LeaguesPayload, MatchListPayload)](model: type[T], result: FetchResult) -> T:
    if not result.ok:
        raise SyncError(f"{result.record.path}: PitchAPI returned {result.error_code}")
    try:
        return model.model_validate(result.data)
    except ValidationError as exc:
        raise SyncError(f"{result.record.path}: unexpected response shape\n{exc}") from exc


def sync_match_list(
    fetcher: CachedFetcher, session: Session, league_id: str, season_label: str
) -> MatchListSummary:
    leagues = _parse(LeaguesPayload, fetcher.get(build_path("leagues"), policy=LEAGUES))
    league = next((lg for lg in leagues.leagues if lg.id == league_id), None)
    if league is None:
        raise SyncError(f"no league with ID {league_id!r} (see `/v1/leagues`)")
    if season_label not in league.seasons:
        raise SyncError(
            f"{league.name} ({league.country_code}) has no season {season_label!r}; "
            f"available: {', '.join(league.seasons)}"
        )

    policy = MATCH_LIST_CURRENT if season_label == league.latest_season else MATCH_LIST_COMPLETED
    result = fetcher.get(
        build_path("leagues", league_id, "matches"),
        {"season": season_label, "status": MATCH_LIST_STATUS},
        policy=policy,
    )
    payload = _parse(MatchListPayload, result)
    if payload.league.season != season_label:
        raise SyncError(f"asked for season {season_label!r}, got {payload.league.season!r}")

    summary = normalize_match_list(session, league, payload, fetched_at=result.record.fetched_at)
    summary.match_list_from_cache = result.from_cache
    return summary
