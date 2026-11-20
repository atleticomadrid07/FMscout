"""Sync one match's per-player data into scouting.db (slice 1c).

Three endpoints, joined by PitchAPI player ID:
- /players          who played (minutes_played present), team, minutes, rating, basic stats
- /lineups          starter or sub, slot code, formation → role and side
- /advanced/players minutes_actual and advanced stats (may be ANALYTICS_UNAVAILABLE)

Each match is normalized in the caller's transaction: appearances are upserted (and
removed if they disappeared), and the match's stat rows are deleted and re-inserted.
Registry absence policies decide what a missing key becomes (0, NULL, or 0 of 0), and
`applies_to` keeps metrics off players they don't describe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.cache import FINISHED_MATCH, CachedFetcher
from scouting.adapters.pitchapi.client import build_path
from scouting.adapters.pitchapi.raw_store import RawRecord
from scouting.adapters.pitchapi.schemas import (
    AdvancedPayload,
    AdvancedRow,
    LineupEntry,
    LineupsPayload,
    PlayersPayload,
    PlayerStatRow,
    StatValue,
)
from scouting.db.models import (
    Appearance,
    Match,
    MatchSourceId,
    Player,
    PlayerSourceId,
    Team,
    TeamSourceId,
    to_utc_naive,
)
from scouting.db.models import (
    StatValue as StatValueRow,
)
from scouting.domain.names import search_name
from scouting.domain.positions import GK_SLOT, SUB_CATEGORY_GK, role_for
from scouting.ingest.match_list import SOURCE, SyncError, UpsertCounts
from scouting.ingest.reference_data import sync_reference_data
from scouting.metrics.registry import KEEPER_ONLY_KEYS, KNOWN_PLAYER_KEYS, REGISTRY, MetricDef

logger = logging.getLogger(__name__)

PLAYERS_SOURCE_KEYS = frozenset(m.source_key for m in REGISTRY if m.source == "pitchapi_players")


@dataclass(slots=True)
class MatchDetailSummary:
    label: str
    players_status: str
    advanced_status: str
    lineups_status: str
    appearances: UpsertCounts = field(default_factory=UpsertCounts)
    appearances_removed: int = 0
    stat_values: int = 0
    goalkeepers: int = 0
    starters: int = 0
    unmapped_slots: list[tuple[str | None, int]] = field(default_factory=list)
    unknown_keys: set[str] = field(default_factory=set)
    missing_advanced_rows: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _LineupInfo:
    entry: LineupEntry
    is_starter: bool
    formation: str | None


# --- helpers ------------------------------------------------------------------------------------


def _number(raw: Any, *, where: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise SyncError(f"{where}: expected a number, got {raw!r}")
    return float(raw)


def _applies(metric: MetricDef, is_goalkeeper: bool) -> bool:
    if metric.applies_to == "all":
        return True
    return is_goalkeeper if metric.applies_to == "gk" else not is_goalkeeper


def _absent(metric: MetricDef) -> tuple[float | None, float | None]:
    if metric.absence_policy == "zero":
        return 0.0, None
    if metric.absence_policy == "fraction_zero_attempts":
        return 0.0, 0.0
    return None, None


def players_metric_value(
    metric: MetricDef, flat: dict[str, StatValue], where: str
) -> tuple[float | None, float | None]:
    """(value, denominator) for a /players metric, applying its absence policy."""
    stat = flat.get(metric.source_key)
    if stat is None:
        return _absent(metric)
    here = f"{where} {metric.source_key}"
    if metric.is_fraction:
        if stat.type != "fractionWithPercentage":
            raise SyncError(f"{here}: expected a fraction, got type {stat.type!r}")
        return _number(stat.value, where=here), _number(stat.total, where=here)
    if stat.type not in ("integer", "double"):
        raise SyncError(f"{here}: expected integer/double, got type {stat.type!r}")
    return _number(stat.value, where=here), None  # a provider null stays NULL


def advanced_metric_value(
    metric: MetricDef, row: AdvancedRow, where: str
) -> tuple[float | None, float | None] | None:
    """(value, denominator) for an advanced metric, or None when the metric's group is
    null for this player (e.g. `goalkeeping` for a sub keeper): no data, so no row."""
    group = metric.source_key.split(".", 1)[0]
    found_group, group_value = row.lookup(group)
    if not found_group or group_value is None:
        return None
    here = f"{where} {metric.source_key}"
    found, raw = row.lookup(metric.source_key)
    if metric.denominator_key is None:
        return _number(raw, where=here) if found else _absent(metric)[0], None
    found_denominator, raw_denominator = row.lookup(metric.denominator_key)
    denominator = _number(raw_denominator, where=here) if found_denominator else None
    if denominator is None:
        return _absent(metric)
    if metric.value_is_rate:
        # Weighted average: store rate x weight over weight. With no weight the rate is
        # undefined and contributes nothing (0 of 0); an unknown rate stays unknown.
        if denominator == 0:
            return 0.0, 0.0
        rate = _number(raw, where=here) if found else None
        return (None, None) if rate is None else (rate * denominator, denominator)
    return (_number(raw, where=here) if found else 0.0), denominator


def _is_goalkeeper(lineup: _LineupInfo | None, flat: dict[str, StatValue]) -> bool:
    """Starter in slot 11, or sub with category 0; without lineup data, keeper-only stats."""
    if lineup is not None:
        gk_code = GK_SLOT if lineup.is_starter else SUB_CATEGORY_GK
        return lineup.entry.position_id == gk_code
    return any(key in flat for key in KEEPER_ONLY_KEYS)


def _parse[T: (PlayersPayload, AdvancedPayload, LineupsPayload)](
    model: type[T], record: RawRecord
) -> T | None:
    if not record.ok:
        return None
    try:
        return model.model_validate(record.data)
    except ValidationError as exc:
        raise SyncError(f"{record.path}: unexpected response shape\n{exc}") from exc


def _upsert_player(session: Session, row: PlayerStatRow) -> Player:
    """Insert new players; for known ones only fill missing fields (profiles in 1d are the
    authority, and an old season's data must not overwrite newer details)."""
    player = session.scalars(
        select(Player)
        .join(PlayerSourceId, PlayerSourceId.player_id == Player.id)
        .where(PlayerSourceId.source == SOURCE, PlayerSourceId.external_id == row.player.id)
    ).one_or_none()
    if player is None:
        player = Player(
            origin="real",
            name=row.player.name,
            search_name=search_name(row.player.name),
            image_url=row.player.image_url,
        )
        session.add(player)
        session.flush()
        session.add(PlayerSourceId(source=SOURCE, external_id=row.player.id, player_id=player.id))
    elif player.image_url is None and row.player.image_url is not None:
        player.image_url = row.player.image_url
    return player


def _team_ids(session: Session, match: Match) -> dict[str, int]:
    """PitchAPI team ID → internal ID, for this match's two teams only."""
    rows = session.execute(
        select(TeamSourceId.external_id, TeamSourceId.team_id).where(
            TeamSourceId.source == SOURCE,
            TeamSourceId.team_id.in_((match.home_team_id, match.away_team_id)),
        )
    ).all()
    return {external: internal for external, internal in rows}


def _lineup_index(lineups: LineupsPayload | None) -> dict[str, _LineupInfo]:
    index: dict[str, _LineupInfo] = {}
    if lineups is None:
        return index
    for side in (lineups.home, lineups.away):
        for entries, is_starter in ((side.starters, True), (side.subs, False)):
            for entry in entries:
                if entry.player_id in index:
                    raise SyncError(f"lineups list player {entry.player_id} twice")
                index[entry.player_id] = _LineupInfo(entry, is_starter, side.formation)
    return index


def _match_label(session: Session, match: Match) -> str:
    home = session.get(Team, match.home_team_id)
    away = session.get(Team, match.away_team_id)
    names = (home.name if home else "?", away.name if away else "?")
    return f"{names[0]} v {names[1]} · {match.match_date.isoformat()}"


# --- normalize ------------------------------------------------------------------------------------


def normalize_match(
    session: Session,
    match: Match,
    *,
    players: RawRecord,
    advanced: RawRecord,
    lineups: RawRecord,
) -> MatchDetailSummary:
    metric_ids = sync_reference_data(session)

    player_rows = _parse(PlayersPayload, players)
    advanced_payload = _parse(AdvancedPayload, advanced)
    lineups_payload = _parse(LineupsPayload, lineups)

    summary = MatchDetailSummary(
        label=_match_label(session, match),
        players_status="ok" if player_rows is not None else "missing",
        advanced_status="ok" if advanced_payload is not None else "unavailable",
        lineups_status="ok" if lineups_payload is not None else "missing",
    )
    advanced_rows = (
        {row.player.id: row for row in advanced_payload.players} if advanced_payload else {}
    )
    lineup_index = _lineup_index(lineups_payload)
    team_ids = _team_ids(session, match)

    existing = {
        a.player_id: a
        for a in session.scalars(select(Appearance).where(Appearance.match_id == match.id))
    }
    session.execute(
        delete(StatValueRow).where(
            StatValueRow.appearance_id.in_(
                select(Appearance.id).where(Appearance.match_id == match.id)
            )
        )
    )

    stat_rows: list[dict[str, Any]] = []
    seen_players: set[int] = set()
    for row in player_rows.root if player_rows is not None else []:
        where = f"match {players.path} player {row.player.id}"
        try:
            flat = row.flat_stats()
        except ValueError as exc:
            raise SyncError(str(exc)) from exc
        summary.unknown_keys |= set(flat) - KNOWN_PLAYER_KEYS - PLAYERS_SOURCE_KEYS

        minutes_stat = flat.get("minutes_played")
        minutes = None if minutes_stat is None else _number(minutes_stat.value, where=where)
        if minutes is None:
            continue  # unused substitute: no appearance (SCHEMA §11, Q6)
        team_id = team_ids.get(row.team_id)
        if team_id is None:
            raise SyncError(f"{where}: team {row.team_id} isn't one of this match's teams")

        player = _upsert_player(session, row)
        if player.id in seen_players:
            raise SyncError(f"{where}: player listed twice")
        seen_players.add(player.id)

        lineup = lineup_index.get(row.player.id)
        is_goalkeeper = _is_goalkeeper(lineup, flat)
        role_side = None
        if lineup is not None and lineup.is_starter:
            role_side = role_for(lineup.formation, lineup.entry.position_id)
            if role_side is None:
                summary.unmapped_slots.append((lineup.formation, lineup.entry.position_id))
            summary.starters += 1
        summary.goalkeepers += is_goalkeeper

        advanced_row = advanced_rows.get(row.player.id)
        if advanced_payload is not None and advanced_row is None:
            summary.missing_advanced_rows.append(row.player.name)
        rating_stat = flat.get("rating_title")

        values: dict[str, object] = {
            "team_id": team_id,
            "is_starter": None if lineup is None else lineup.is_starter,
            "slot_code": lineup.entry.position_id if lineup and lineup.is_starter else None,
            "sub_category": lineup.entry.position_id if lineup and not lineup.is_starter else None,
            "formation": None if lineup is None else lineup.formation,
            "role": None if role_side is None else role_side[0],
            "side": None if role_side is None else role_side[1],
            "is_goalkeeper": is_goalkeeper,
            "minutes": int(minutes),
            "minutes_actual": None if advanced_row is None else advanced_row.minutes_played,
            "rating": None if rating_stat is None else _number(rating_stat.value, where=where),
        }
        appearance = existing.pop(player.id, None)
        if appearance is None:
            appearance = Appearance(match_id=match.id, player_id=player.id, **values)
            session.add(appearance)
            session.flush()
            summary.appearances.add("inserted")
        else:
            changed = False
            for name, value in values.items():
                if getattr(appearance, name) != value:
                    setattr(appearance, name, value)
                    changed = True
            summary.appearances.add("updated" if changed else "unchanged")

        for metric in REGISTRY:
            if not _applies(metric, is_goalkeeper):
                continue
            if metric.source == "pitchapi_players":
                value, denominator = players_metric_value(metric, flat, where)
            elif advanced_row is not None:
                advanced_value = advanced_metric_value(metric, advanced_row, where)
                if advanced_value is None:
                    continue  # the metric's group is null for this player: no row
                value, denominator = advanced_value
            else:
                continue  # advanced unavailable for this match/player: no row, no exposure
            stat_rows.append(
                {
                    "appearance_id": appearance.id,
                    "metric_id": metric_ids[metric.code],
                    "value": value,
                    "denominator": denominator,
                }
            )

    if player_rows is not None:
        # Appearances no longer in the data (e.g. a provider correction) are removed.
        for stale in existing.values():
            session.delete(stale)
            summary.appearances_removed += 1
    if stat_rows:
        session.execute(insert(StatValueRow), stat_rows)
    summary.stat_values = len(stat_rows)

    if lineups_payload is not None:
        match.home_formation = lineups_payload.home.formation
        match.away_formation = lineups_payload.away.formation
    match.players_status = summary.players_status
    match.advanced_status = summary.advanced_status
    match.lineups_status = summary.lineups_status
    # The newest fetch time of the three responses, so a re-run from cache is identical.
    match.normalized_at = to_utc_naive(max(r.fetched_at for r in (players, advanced, lineups)))
    session.flush()

    # DEBUG, not WARNING: a season sync would repeat these for every match. Both CLI
    # commands print them once, with counts, in their summary.
    for key in sorted(summary.unknown_keys):
        logger.debug("new /players stat key not in the registry or known list: %s", key)
    for formation, slot in summary.unmapped_slots:
        logger.debug("no role mapping for formation %s slot %s", formation, slot)
    return summary


# --- fetch + normalize ----------------------------------------------------------------------------


def find_match(session: Session, match_external_id: str) -> Match:
    match = session.scalars(
        select(Match)
        .join(MatchSourceId, MatchSourceId.match_id == Match.id)
        .where(MatchSourceId.source == SOURCE, MatchSourceId.external_id == match_external_id)
    ).one_or_none()
    if match is None:
        raise SyncError(
            f"match {match_external_id!r} isn't in scouting.db; "
            "run `scouting sync matches` for its league and season first"
        )
    return match


def sync_match(
    fetcher: CachedFetcher, session: Session, match_external_id: str
) -> MatchDetailSummary:
    match = find_match(session, match_external_id)
    if match.status != "finished":
        raise SyncError(f"match {match_external_id} has status {match.status!r}, not finished")
    base = ("matches", match_external_id)
    players = fetcher.get(build_path(*base, "players"), policy=FINISHED_MATCH)
    advanced = fetcher.get(build_path(*base, "advanced", "players"), policy=FINISHED_MATCH)
    lineups = fetcher.get(build_path(*base, "lineups"), policy=FINISHED_MATCH)
    return normalize_match(
        session, match, players=players.record, advanced=advanced.record, lineups=lineups.record
    )
