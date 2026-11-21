"""Sync a whole league-season (slice 1d): match list → every match → player profiles.

Resumable by design:
- every API response is written to raw.db as soon as it arrives (slice 1a);
- each match is normalized and committed in its own transaction, so a crash or Ctrl-C
  rolls back only the match in progress;
- the next run works through the matches whose data isn't all `ok` yet.

`renormalize=True` re-processes every match of the season (from cache when offline),
which is how a registry change reaches matches that were already synced.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, Engine, func, or_, select
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.cache import CachedFetcher
from scouting.db.models import (
    Appearance,
    Competition,
    CompetitionSourceId,
    Match,
    MatchSourceId,
    Season,
    SyncState,
)
from scouting.ingest.match_detail import MatchDetailSummary, sync_match
from scouting.ingest.match_list import SOURCE, MatchListSummary, SyncError, sync_match_list
from scouting.ingest.profiles import ProfileSummary, sync_profiles
from scouting.ingest.progress import Progress

logger = logging.getLogger(__name__)

PROGRESS_EVERY = 10


@dataclass(slots=True)
class SeasonSummary:
    match_list: MatchListSummary
    to_process: int = 0
    processed: int = 0
    advanced_unavailable: int = 0
    players_missing: int = 0
    lineups_missing: int = 0
    heatmaps_missing: int = 0
    heatmaps_pending: int = 0  # offline, never fetched (slice 2d)
    stat_values: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (match ID, error)
    unmapped_slots: Counter[tuple[str | None, int]] = field(default_factory=Counter)
    unknown_keys: set[str] = field(default_factory=set)
    profiles: ProfileSummary | None = None
    interrupted: bool = False
    still_pending: int = 0

    def add(self, match: MatchDetailSummary) -> None:
        self.processed += 1
        self.advanced_unavailable += match.advanced_status == "unavailable"
        self.players_missing += match.players_status == "missing"
        self.lineups_missing += match.lineups_status == "missing"
        self.heatmaps_missing += match.heatmaps_status == "missing"
        self.heatmaps_pending += match.heatmaps_status == "pending"
        self.stat_values += match.stat_values
        self.unmapped_slots.update(match.unmapped_slots)
        self.unknown_keys |= match.unknown_keys


def find_season(session: Session, league_id: str, label: str) -> Season:
    season = session.scalars(
        select(Season)
        .join(Competition, Competition.id == Season.competition_id)
        .join(CompetitionSourceId, CompetitionSourceId.competition_id == Competition.id)
        .where(
            CompetitionSourceId.source == SOURCE,
            CompetitionSourceId.external_id == league_id,
            Season.label == label,
        )
    ).one_or_none()
    if season is None:
        raise SyncError(f"season {label!r} of {league_id} isn't in scouting.db")
    return season


def needs_work() -> ColumnElement[bool]:
    """A match whose per-match data isn't all `ok` (pending, missing or unavailable)."""
    return or_(
        Match.players_status != "ok",
        Match.advanced_status != "ok",
        Match.lineups_status != "ok",
        Match.heatmaps_status != "ok",
    )


def _matches_to_process(session: Session, season_id: int, *, renormalize: bool) -> list[str]:
    stmt = (
        select(MatchSourceId.external_id)
        .join(Match, Match.id == MatchSourceId.match_id)
        .where(MatchSourceId.source == SOURCE, Match.season_id == season_id)
        .order_by(Match.kickoff_utc, Match.id)
    )
    if not renormalize:
        stmt = stmt.where(needs_work())
    return list(session.scalars(stmt))


def sync_season(
    fetcher: CachedFetcher,
    engine: Engine,
    league_id: str,
    season_label: str,
    *,
    limit: int | None = None,
    renormalize: bool = False,
    profiles: bool = True,
    on_progress: Callable[[Progress], None] | None = None,
) -> SeasonSummary:
    with Session(engine) as session, session.begin():
        match_list = sync_match_list(fetcher, session, league_id, season_label)
        season_id = find_season(session, league_id, season_label).id
        to_process = _matches_to_process(session, season_id, renormalize=renormalize)

    summary = SeasonSummary(match_list=match_list)
    batch = to_process if limit is None else to_process[:limit]
    summary.to_process = len(batch)
    started = time.monotonic()

    try:
        for index, match_id in enumerate(batch, start=1):
            try:
                with Session(engine) as session, session.begin():
                    summary.add(sync_match(fetcher, session, match_id))
            except SyncError as exc:
                # A data problem in one match shouldn't block the season. The match keeps
                # its statuses, so the next run retries it; the run reports it at the end.
                logger.error("match %s failed: %s", match_id, exc)
                summary.failed.append((match_id, str(exc)))
            if on_progress is not None and (index % PROGRESS_EVERY == 0 or index == len(batch)):
                on_progress(Progress(index, len(batch), time.monotonic() - started))
    except KeyboardInterrupt:
        summary.interrupted = True

    if profiles and not summary.interrupted:
        with Session(engine) as session:
            season_players = list(
                session.scalars(
                    select(Appearance.player_id)
                    .join(Match, Match.id == Appearance.match_id)
                    .where(Match.season_id == season_id)
                    .distinct()
                )
            )
        summary.profiles = sync_profiles(fetcher, engine, season_players, on_progress=on_progress)
        summary.interrupted = summary.profiles.interrupted

    with Session(engine) as session, session.begin():
        summary.still_pending = (
            session.scalar(
                select(func.count())
                .select_from(Match)
                .where(Match.season_id == season_id, Match.players_status == "pending")
            )
            or 0
        )
        if summary.still_pending == 0 and not summary.interrupted:
            state = session.get(SyncState, season_id)
            if state is not None:
                # Deterministic: the newest per-match fetch time, not the clock.
                state.last_full_sync_at = session.scalar(
                    select(func.max(Match.normalized_at)).where(Match.season_id == season_id)
                )
    return summary
