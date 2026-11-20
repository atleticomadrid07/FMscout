"""`scouting` command-line interface (Typer). Run from backend/: `uv run scouting --help`.

Only this CLI talks to PitchAPI (D-004); the web app reads scouting.db only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Annotated

import typer
from sqlalchemy import Engine
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
from scouting.db.reports import checksum, formation_report
from scouting.db.schema_version import SchemaOutOfDateError, check_schema_current
from scouting.domain.positions import FORMATION_ROLES
from scouting.ingest.match_detail import MatchDetailSummary, sync_match
from scouting.ingest.match_list import MatchListSummary, SyncError, UpsertCounts, sync_match_list
from scouting.ingest.profiles import ProfileSummary, sync_profiles
from scouting.ingest.progress import Progress
from scouting.ingest.season import SeasonSummary, find_season, sync_season
from scouting.metrics.registry import BY_CODE

app = typer.Typer(no_args_is_help=True, help="FM Scout backend commands.")
sync_app = typer.Typer(no_args_is_help=True, help="Fetch PitchAPI data into the local databases.")
inspect_app = typer.Typer(no_args_is_help=True, help="Show what's stored (read-only).")
report_app = typer.Typer(no_args_is_help=True, help="Summaries of what's stored (read-only).")
app.add_typer(sync_app, name="sync")
app.add_typer(inspect_app, name="inspect")
app.add_typer(report_app, name="report")

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
    if level != "DEBUG":
        # The schema check runs Alembic on every command; its INFO lines are noise here.
        logging.getLogger("alembic").setLevel(logging.WARNING)


def _fail(exc: Exception) -> typer.Exit:
    typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


@contextmanager
def _sync_context(offline: bool) -> Iterator[tuple[CachedFetcher, Engine]]:
    """Settings, schema check, raw.db and the client (unless offline)."""
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
            yield CachedFetcher(store, client), engine
    finally:
        engine.dispose()


@contextmanager
def _sync_session(offline: bool) -> Iterator[tuple[CachedFetcher, Session]]:
    """Like _sync_context, with one transaction around the whole command."""
    with _sync_context(offline) as (fetcher, engine), Session(engine) as session, session.begin():
        yield fetcher, session


@contextmanager
def _read_engine() -> Iterator[Engine]:
    settings = Settings()
    _configure_logging(settings.log_level)
    engine = make_engine(settings.database_url)
    try:
        check_schema_current(engine)
        yield engine
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


def _print_match_list(
    summary: MatchListSummary, stats: FetchStats, offline: bool, *, requests: bool = True
) -> None:
    kind = "current season" if summary.is_current else "completed season"
    source = "cache" if summary.match_list_from_cache else "PitchAPI"
    typer.echo(f"{summary.competition} {summary.season} ({kind}; match list from {source})")
    typer.echo(_counts("teams", summary.teams))
    typer.echo(_counts("matches", summary.matches))
    if requests:  # the season summary prints it once, at the end
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
    return str(int(value)) if value == int(value) else f"{value:.4g}"


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


# --- season, profiles -----------------------------------------------------------------------


def _minutes(seconds: float | None) -> str:
    return "?" if seconds is None else f"~{max(1, round(seconds / 60))} min"


def _print_progress(progress: Progress) -> None:
    left = _minutes(progress.remaining_s)
    typer.echo(f"  {progress.done}/{progress.total} {progress.unit} · {left} left")


def _print_profiles(p: ProfileSummary) -> None:
    typer.echo(
        f"  profiles: {p.total} players · {p.updated} updated, {p.unchanged} unchanged, "
        f"{p.not_found} not found" + (f", {p.not_cached} not cached" if p.not_cached else "")
    )
    for player_id, error in p.failed:
        typer.secho(f"  FAILED profile {player_id}: {error.splitlines()[0]}", fg="red")


def _print_season(summary: SeasonSummary, stats: FetchStats, offline: bool) -> None:
    _print_match_list(summary.match_list, stats, offline, requests=False)
    typer.echo(
        f"  processed {summary.processed}/{summary.to_process} matches · "
        f"{summary.stat_values} stat values"
    )
    for label, count in (
        ("advanced unavailable", summary.advanced_unavailable),
        ("players missing", summary.players_missing),
        ("lineups missing", summary.lineups_missing),
    ):
        if count:
            typer.secho(f"  {label}: {count} matches", fg="yellow")
    if summary.profiles is not None:
        _print_profiles(summary.profiles)
    for (formation, slot), count in sorted(summary.unmapped_slots.items(), key=str):
        typer.secho(f"  no role mapping: formation {formation} slot {slot} ({count}x)", fg="yellow")
    if summary.unknown_keys:
        keys = ", ".join(sorted(summary.unknown_keys))
        typer.secho(f"  new /players keys (not ingested): {keys}", fg="yellow")
    for match_id, error in summary.failed:
        typer.secho(f"  FAILED {match_id}: {error.splitlines()[0]}", fg="red")
    if summary.interrupted:
        typer.secho("  interrupted: run the same command again to resume", fg="yellow")
    elif summary.still_pending:
        typer.echo(f"  {summary.still_pending} matches still to sync: run again to continue")
    typer.echo(_requests(stats, offline))


@sync_app.command("season")
def sync_whole_season(
    league: Annotated[
        str, typer.Option(help="PitchAPI league ID, e.g. l_4WFCIZ (Premier League).")
    ],
    season: Annotated[
        str, typer.Option(help='Season label as PitchAPI lists it, e.g. "2025/2026".')
    ],
    limit: Annotated[
        int | None, typer.Option(min=1, help="Process at most this many matches, then stop.")
    ] = None,
    renormalize: Annotated[
        bool,
        typer.Option(help="Re-process every match (use with --offline after a registry change)."),
    ] = False,
    offline: OfflineOption = False,
) -> None:
    """Sync a whole league-season: match list, every match, then player profiles.
    Safe to stop (Ctrl-C) and run again: it resumes where it stopped."""
    try:
        with _sync_context(offline) as (fetcher, engine):
            summary = sync_season(
                fetcher, engine, league, season,
                limit=limit, renormalize=renormalize, on_progress=_print_progress,
            )  # fmt: skip
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    _print_season(summary, fetcher.stats, offline)
    if summary.interrupted:
        raise typer.Exit(code=130)
    if summary.failed or (summary.profiles is not None and summary.profiles.failed):
        raise typer.Exit(code=1)


@sync_app.command("profiles")
def sync_all_profiles(offline: OfflineOption = False) -> None:
    """Fetch or refresh player profiles (90-day freshness) for every player in scouting.db.
    Saves every 25 players: safe to stop (Ctrl-C) and run again."""
    try:
        with _sync_context(offline) as (fetcher, engine):
            summary = sync_profiles(fetcher, engine, on_progress=_print_progress)
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    _print_profiles(summary)
    if summary.interrupted:
        typer.secho("  interrupted: run the same command again to resume", fg="yellow")
    typer.echo(_requests(fetcher.stats, offline))
    if summary.interrupted:
        raise typer.Exit(code=130)
    if summary.failed:
        raise typer.Exit(code=1)


# --- reports ----------------------------------------------------------------------------------


@report_app.command("formations")
def report_formations(
    league: Annotated[str, typer.Option(help="PitchAPI league ID, e.g. l_4WFCIZ.")],
    season: Annotated[str, typer.Option(help='Season label, e.g. "2025/2026".')],
) -> None:
    """Every formation used in a season, and any (formation, slot) without a role mapping."""
    try:
        with _read_engine() as engine, Session(engine) as session:
            report = formation_report(session, find_season(session, league, season).id)
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"{report.matches} matches · formations by team-matches:")
    for formation, count in report.formations.most_common():
        mapped = "" if formation in FORMATION_ROLES else "   <- not mapped"
        typer.echo(f"  {formation:<10} {count:>4}{mapped}")
    if report.unmapped_slots:
        typer.secho(
            f"{report.starters_without_role} starter appearances without a role:", fg="yellow"
        )
        for (slot_formation, slot), count in report.unmapped_slots.most_common():
            typer.echo(f"  formation {slot_formation} slot {slot}: {count}")
    else:
        typer.echo("every starter has a role")


@report_app.command("checksum")
def report_checksum(
    tables: Annotated[bool, typer.Option(help="Also print row counts per table.")] = False,
) -> None:
    """A hash of every canonical table: equal hashes mean identical data."""
    try:
        with _read_engine() as engine:
            digest, counts = checksum(engine)
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(digest)
    if tables:
        for name, count in counts.items():
            typer.echo(f"  {name:<22} {count:>9}")


def main() -> None:  # pragma: no cover - console-script shim
    app()
