"""Player profiles (GET /v1/players/{id}), slice 1d.

The profile is the authority on a player's name, short name (used by search), country,
shirt number and listed position. Match data only fills fields that are still empty
(slice 1c). `profile_fetched_at` is the response's fetch time, not the clock, so a
rebuild from raw.db gives an identical database.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Final, Literal

from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.cache import PROFILE, CachedFetcher, CacheMissError
from scouting.adapters.pitchapi.client import build_path
from scouting.adapters.pitchapi.raw_store import RawRecord
from scouting.adapters.pitchapi.schemas import PlayerProfile
from scouting.db.models import Player, PlayerSourceId, to_utc_naive
from scouting.domain.names import search_name
from scouting.ingest.match_list import SOURCE, SyncError
from scouting.ingest.progress import Progress

logger = logging.getLogger(__name__)

PROFILE_CHUNK: Final = 25  # players per transaction (about 25 s online)


@dataclass(slots=True)
class ProfileSummary:
    updated: int = 0
    unchanged: int = 0
    not_found: int = 0
    not_cached: int = 0  # offline runs only
    failed: list[tuple[str, str]] = field(default_factory=list)  # (player ID, error)
    interrupted: bool = False

    @property
    def total(self) -> int:
        return self.updated + self.unchanged + self.not_found + self.not_cached + len(self.failed)


def apply_profile(player: Player, record: RawRecord) -> bool:
    """Write a profile response onto a player; return whether anything changed."""
    try:
        profile = PlayerProfile.model_validate(record.data)
    except ValidationError as exc:
        raise SyncError(f"{record.path}: unexpected response shape\n{exc}") from exc
    values: dict[str, object] = {
        "name": profile.name,
        "search_name": search_name(profile.name),
        "short_name": profile.short_name,
        "country_code": profile.country_code,
        "shirt_number": None if profile.shirt_number is None else str(profile.shirt_number),
        "listed_slot_code": profile.position_id,
        "profile_fetched_at": to_utc_naive(record.fetched_at),
    }
    if profile.image_url is not None:
        values["image_url"] = profile.image_url
    changed = False
    for name, value in values.items():
        if getattr(player, name) != value:
            setattr(player, name, value)
            changed = True
    return changed


def _sync_one(
    fetcher: CachedFetcher, session: Session, player_id: int, external_id: str
) -> Literal["updated", "unchanged", "not_found", "not_cached"]:
    try:
        result = fetcher.get(build_path("players", external_id), policy=PROFILE)
    except CacheMissError:
        return "not_cached"
    if not result.ok:
        logger.info("no profile for %s (%s)", external_id, result.error_code)
        return "not_found"
    player = session.get_one(Player, player_id)
    return "updated" if apply_profile(player, result.record) else "unchanged"


def sync_profiles(
    fetcher: CachedFetcher,
    engine: Engine,
    player_ids: Iterable[int] | None = None,
    *,
    chunk_size: int = PROFILE_CHUNK,
    on_progress: Callable[[Progress], None] | None = None,
) -> ProfileSummary:
    """Fetch (or read from cache) and apply profiles for the given players, or all players.

    Freshness is the fetcher's job (PROFILE: 90 days; 404s re-checked after 30). Commits
    every `chunk_size` players, so a stop keeps the work done; fetched responses are in
    raw.db either way. A profile with an unexpected shape is reported and skipped.
    """
    stmt = (
        select(Player.id, PlayerSourceId.external_id)
        .join(PlayerSourceId, PlayerSourceId.player_id == Player.id)
        .where(PlayerSourceId.source == SOURCE)
        .order_by(Player.id)
    )
    if player_ids is not None:
        stmt = stmt.where(Player.id.in_(list(player_ids)))
    with Session(engine) as session:
        targets = [(row.id, row.external_id) for row in session.execute(stmt)]

    summary = ProfileSummary()
    started = time.monotonic()
    try:
        for start in range(0, len(targets), chunk_size):
            chunk = targets[start : start + chunk_size]
            outcomes: Counter[str] = Counter()
            failed: list[tuple[str, str]] = []
            with Session(engine) as session, session.begin():
                for player_id, external_id in chunk:
                    try:
                        # apply_profile validates before it changes anything, so a bad
                        # profile leaves its player untouched.
                        outcomes[_sync_one(fetcher, session, player_id, external_id)] += 1
                    except SyncError as exc:
                        logger.error("profile %s failed: %s", external_id, exc)
                        failed.append((external_id, str(exc)))
            # Counted only once the chunk is committed, so the summary matches the database.
            summary.updated += outcomes["updated"]
            summary.unchanged += outcomes["unchanged"]
            summary.not_found += outcomes["not_found"]
            summary.not_cached += outcomes["not_cached"]
            summary.failed.extend(failed)
            if on_progress is not None:
                done = start + len(chunk)
                on_progress(Progress(done, len(targets), time.monotonic() - started, "players"))
    except KeyboardInterrupt:
        summary.interrupted = True
    return summary
