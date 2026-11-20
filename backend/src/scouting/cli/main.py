"""`scouting` command-line interface (Typer). Run from backend/: `uv run scouting --help`.

Only this CLI talks to PitchAPI (D-004); the web app reads scouting.db only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.cache import (
    CachedFetcher,
    CacheMissError,
    FetchStats,
    cache_key,
)
from scouting.adapters.pitchapi.client import PitchApiClient, build_path
from scouting.adapters.pitchapi.errors import PitchApiError
from scouting.adapters.pitchapi.raw_store import RawStore, RawStoreError
from scouting.adapters.pitchapi.schemas import AdvancedPayload, PlayersPayload
from scouting.config.settings import MissingApiKeyError, Settings
from scouting.db.base import make_engine
from scouting.db.queries import AppearanceDetail, MetricLine, appearance_detail
from scouting.db.schema_version import SchemaOutOfDateError, check_schema_current
from scouting.ingest.match_detail import MatchDetailSummary, sync_match
from scouting.ingest.match_list import MatchListSummary, SyncError, UpsertCounts, sync_match_list
from scouting.metrics.registry import BY_CODE

app = typer.Typer(no_args_is_help=True, help="FM Scout backend commands.")
sync_app = typer.Typer(no_args_is_help=True, help="Fetch PitchAPI data into the local databases.")
inspect_app = typer.Typer(no_args_is_help=True, help="Show what's stored (read-only).")
app.add_typer(sync_app, name="sync")
app.add_typer(inspect_app, name="inspect")

# Errors we expect and can explain in one line; anything else gets a full traceback.
EXPECTED_ERRORS = (
    MissingApiKeyError,
    SchemaOutOfDateError,
    SyncError,
    CacheMissError,
    RawStoreError,
    PitchApiError,
)

OfflineOption = Annotated[
    bool, typer.Option(help="Use cached responses only; never call PitchAPI.")
]


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _fail(exc: Exception) -> typer.Exit:
    typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


@contextmanager
def _sync_session(offline: bool) -> Iterator[tuple[CachedFetcher, Session]]:
    """Settings, schema check, raw.db, client (unless offline) and one transaction."""
    settings = Settings()
    _configure_logging(settings.log_level)
    engine = make_engine(settings.database_url)
    try:
        check_schema_current(engine)
        with ExitStack() as stack:
            store = stack.enter_context(RawStore(settings.raw_db_path))
            client = (
                None
                if offline
                else stack.enter_context(
                    PitchApiClient(
                        settings.require_api_key(),
                        min_interval_s=settings.min_request_interval_s,
                    )
                )
            )
            fetcher = CachedFetcher(store, client)
            with Session(engine) as session, session.begin():
                yield fetcher, session
    finally:
        engine.dispose()


def _counts(label: str, counts: UpsertCounts, width: int = 8) -> str:
    return (
        f"  {label:<{width}} {counts.total:>4} total: {counts.inserted} new, "
        f"{counts.updated} updated, {counts.unchanged} unchanged"
    )


def _requests(stats: FetchStats, offline: bool) -> str:
    mode = " (offline)" if offline else ""
    return f"  requests: {stats.network_fetches} to PitchAPI, {stats.cache_hits} from cache{mode}"


# --- sync ---------------------------------------------------------------------------------------


def _print_match_list(summary: MatchListSummary, stats: FetchStats, offline: bool) -> None:
    kind = "current season" if summary.is_current else "completed season"
    source = "cache" if summary.match_list_from_cache else "PitchAPI"
    typer.echo(f"{summary.competition} {summary.season} ({kind}; match list from {source})")
    typer.echo(_counts("teams", summary.teams))
    typer.echo(_counts("matches", summary.matches))
    typer.echo(_requests(stats, offline))


@sync_app.command("matches")
def sync_matches(
    league: Annotated[
        str, typer.Option(help="PitchAPI league ID, e.g. l_4WFCIZ (Premier League).")
    ],
    season: Annotated[
        str, typer.Option(help='Season label as PitchAPI lists it, e.g. "2025/2026".')
    ],
    offline: OfflineOption = False,
) -> None:
    """Sync one league-season's competition, teams and match list into scouting.db."""
    try:
        with _sync_session(offline) as (fetcher, session):
            summary = sync_match_list(fetcher, session, league, season)
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    _print_match_list(summary, fetcher.stats, offline)


def _print_match(summary: MatchDetailSummary, stats: FetchStats, offline: bool) -> None:
    typer.echo(summary.label)
    typer.echo(
        f"  data: players {summary.players_status}, advanced {summary.advanced_status}, "
        f"lineups {summary.lineups_status}"
    )
    typer.echo(_counts("appearances", summary.appearances, width=11))
    if summary.appearances_removed:
        typer.echo(f"  removed {summary.appearances_removed} appearances no longer in the data")
    typer.echo(
        f"  {summary.starters} starters, {summary.goalkeepers} goalkeepers, "
        f"{summary.stat_values} stat values"
    )
    for formation, slot in summary.unmapped_slots:
        typer.secho(f"  no role mapping: formation {formation} slot {slot}", fg="yellow")
    if summary.missing_advanced_rows:
        names = ", ".join(summary.missing_advanced_rows)
        typer.secho(f"  no advanced row for: {names}", fg="yellow")
    if summary.unknown_keys:
        keys = ", ".join(sorted(summary.unknown_keys))
        typer.secho(f"  new /players keys (not ingested): {keys}", fg="yellow")
    typer.echo(_requests(stats, offline))


@sync_app.command("match")
def sync_one_match(
    match_id: Annotated[str, typer.Argument(help="PitchAPI match ID, e.g. m_1t3Zja.")],
    offline: OfflineOption = False,
) -> None:
    """Sync one match's players, appearances and stats (the match must already be in the DB)."""
    try:
        with _sync_session(offline) as (fetcher, session):
            summary = sync_match(fetcher, session, match_id)
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    _print_match(summary, fetcher.stats, offline)


# --- inspect ------------------------------------------------------------------------------------


def _format_number(value: float | None) -> str:
    if value is None:
        return "NULL"
    return str(int(value)) if value == int(value) else f"{value:.3f}".rstrip("0")


def _format_stored(line: MetricLine) -> str:
    if line.kind == "fraction":
        return f"{_format_number(line.value)} / {_format_number(line.denominator)}"
    return _format_number(line.value)


def _raw_values(store: RawStore, match_id: str, player_id: str) -> dict[str, str]:
    """What PitchAPI sent for each registry metric, read from raw.db (no API calls)."""
    out: dict[str, str] = {}
    players_record = store.get(cache_key(build_path("matches", match_id, "players")))
    if players_record is not None and players_record.ok:
        rows = PlayersPayload.model_validate(players_record.data).root
        row = next((r for r in rows if r.player.id == player_id), None)
        flat = row.flat_stats() if row is not None else {}
        for metric in BY_CODE.values():
            if metric.source != "pitchapi_players":
                continue
            stat = flat.get(metric.source_key)
            if stat is None:
                out[metric.code] = "(absent)"
            elif stat.total is not None:
                out[metric.code] = f"{stat.value} / {stat.total}"
            else:
                out[metric.code] = "null" if stat.value is None else str(stat.value)
    advanced_record = store.get(cache_key(build_path("matches", match_id, "advanced", "players")))
    if advanced_record is not None and advanced_record.ok:
        advanced = AdvancedPayload.model_validate(advanced_record.data)
        adv_row = next((r for r in advanced.players if r.player.id == player_id), None)
        for metric in BY_CODE.values():
            if metric.source != "pitchapi_advanced" or adv_row is None:
                continue
            found, raw = adv_row.lookup(metric.source_key)
            out[metric.code] = ("null" if raw is None else str(raw)) if found else "(absent)"
    return out


def _print_appearance(detail: AppearanceDetail, raw: dict[str, str]) -> None:
    typer.echo(
        f"{detail.player_name} · {detail.match_label} · {detail.match_date.isoformat()} · "
        f"{detail.competition} {detail.season}"
    )
    if detail.is_starter is None:
        start = "lineup unknown"
    elif detail.is_starter:
        role = f"{detail.role} ({detail.side})" if detail.role else "role unmapped"
        start = f"starter · {detail.formation} slot {detail.slot_code} → {role}"
    else:
        start = f"sub (category {detail.sub_category}) · team formation {detail.formation}"
    kind = "goalkeeper" if detail.is_goalkeeper else "outfield"
    typer.echo(f"  team {detail.team} · {start} · {kind}")
    actual = "n/a" if detail.minutes_actual is None else str(detail.minutes_actual)
    rating = "not rated" if detail.rating is None else f"{detail.rating:g}"
    typer.echo(f"  minutes {detail.minutes} (actual {actual}) · rating {rating}")
    typer.echo("")
    typer.echo(f"  {'metric':<20} {'stored':<12} {'raw from PitchAPI':<18} source")
    for line in detail.metrics:
        source = "players" if line.source == "pitchapi_players" else "advanced"
        typer.echo(
            f"  {line.code:<20} {_format_stored(line):<12} "
            f"{raw.get(line.code, '(not cached)'):<18} {source}: {line.source_key}"
        )


@inspect_app.command("appearance")
def inspect_appearance(
    match: Annotated[str, typer.Option(help="PitchAPI match ID, e.g. m_1t3Zja.")],
    player: Annotated[str, typer.Option(help="PitchAPI player ID, e.g. p_1cvezl.")],
) -> None:
    """Everything stored for one player in one match, next to the raw values from raw.db."""
    settings = Settings()
    _configure_logging(settings.log_level)
    engine = make_engine(settings.database_url)
    try:
        check_schema_current(engine)
        with Session(engine) as session:
            detail = appearance_detail(session, match, player)
        if detail is None:
            raise SyncError(
                f"no appearance for player {player} in match {match} "
                "(not synced yet, or the player didn't play)"
            )
        with RawStore(settings.raw_db_path) as store:
            raw = _raw_values(store, match, player)
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    finally:
        engine.dispose()
    _print_appearance(detail, raw)


def main() -> None:  # pragma: no cover - console-script shim
    app()
