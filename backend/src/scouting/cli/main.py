"""`scouting` command-line interface (Typer). Run from backend/: `uv run scouting --help`.

Only this CLI talks to PitchAPI (D-004); the web app reads scouting.db only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from typing import Annotated, Final

import typer
from sqlalchemy import Engine, select
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
from scouting.db.models import Scope
from scouting.db.queries import (
    AppearanceDetail,
    MetricLine,
    PeerRow,
    PlayerSeason,
    SeasonStatLine,
    appearance_detail,
    find_players,
    metric_by_match,
    peer_ranking,
    player_season,
    threshold_report,
)
from scouting.db.reports import checksum, formation_report
from scouting.db.schema_version import SchemaOutOfDateError, check_schema_current
from scouting.domain.positions import FORMATION_ROLES, PEER_ROLES
from scouting.ingest.match_detail import MatchDetailSummary, sync_match
from scouting.ingest.match_list import MatchListSummary, SyncError, UpsertCounts, sync_match_list
from scouting.ingest.profiles import ProfileSummary, sync_profiles
from scouting.ingest.progress import Progress
from scouting.ingest.reference_data import sync_reference_data
from scouting.ingest.season import SeasonSummary, find_season, sync_season
from scouting.metrics import depth as depth_rules
from scouting.metrics.aggregate import (
    AggregateError,
    AggregateSummary,
    aggregate_season,
)
from scouting.metrics.depth import (
    PITCH_LENGTH_M,
    REFERENCE_LABELS,
    Calibration,
    DepthReportError,
    PlayerLine,
    calibrate,
    histogram,
    label_for,
)
from scouting.metrics.percentiles import SMALL_GROUP
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
    AggregateError,
    DepthReportError,
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
        f"lineups {summary.lineups_status}, heatmaps {summary.heatmaps_status}"
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
    if summary.missing_heatmap_rows:
        names = ", ".join(summary.missing_heatmap_rows)
        typer.secho(f"  no heatmap for: {names}", fg="yellow")
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


def _depth(depth: float | None, actions: int | None, heatmaps_status: str) -> str:
    """One appearance's heatmap depth, or why there isn't one."""
    if heatmaps_status == "pending":
        return "not fetched yet (run an online sync)"
    if heatmaps_status == "missing":
        return "no heatmap for this match"
    if depth is None:
        return (
            "none (no actions on the heatmap)"
            if actions == 0
            else "none (player not on the heatmap)"
        )
    return f"{depth:.3f} (0 = own goal line, 1 = opponent's) from {actions} actions"


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
    typer.echo(f"  depth {_depth(detail.depth, detail.depth_actions, detail.heatmaps_status)}")
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
        ("heatmaps missing", summary.heatmaps_missing),
    ):
        if count:
            typer.secho(f"  {label}: {count} matches", fg="yellow")
    if summary.heatmaps_pending:
        typer.secho(
            f"  heatmaps not fetched yet: {summary.heatmaps_pending} matches "
            "(run the same command without --offline)",
            fg="yellow",
        )
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
            # Rebuild the season's aggregates once every match is in (D-014), so they can't
            # go stale. A partial run (--limit, Ctrl-C, failures) leaves the old ones.
            complete = not (summary.interrupted or summary.still_pending or summary.failed)
            aggregated = _aggregate(engine, league, season) if complete else None
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    _print_season(summary, fetcher.stats, offline)
    if aggregated is not None:
        _print_aggregate(aggregated)
    else:
        typer.echo("  aggregates not rebuilt: the season isn't fully synced yet")
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


def _scope_id(session: Session, league: str, season: str) -> int:
    season_row = find_season(session, league, season)
    scope = session.scalars(select(Scope).where(Scope.season_id == season_row.id)).first()
    if scope is None:
        raise SyncError(f"{season} isn't aggregated yet: run `scouting aggregate`")
    return scope.id


def _format_ranked(value: float, basis: str, unit: str) -> str:
    if basis == "rate":
        return f"{value * 100:.1f}%" if unit == "count" else f"{value:.4g}"
    return f"{value:.4g}"


@report_app.command("peers")
def report_peers(
    league: Annotated[str, typer.Option(help="PitchAPI league ID, e.g. l_4WFCIZ.")],
    season: Annotated[str, typer.Option(help='Season label, e.g. "2025/2026".')],
    role: Annotated[str, typer.Option(help="Peer role: GK, CB, FB, CM, AM, W or ST.")],
    metric: Annotated[str, typer.Option(help="Metric code, e.g. npxg.")],
    top: Annotated[int, typer.Option(min=0, help="How many from the top.")] = 10,
    bottom: Annotated[int, typer.Option(min=0, help="How many from the bottom.")] = 5,
) -> None:
    """One metric ranked within one peer group: the top and the bottom, with group size."""
    role = role.upper()
    if role not in PEER_ROLES:
        raise _fail(SyncError(f"unknown peer role {role!r}: use one of {', '.join(PEER_ROLES)}"))
    if metric not in BY_CODE:
        raise _fail(SyncError(f"unknown metric code: {metric}"))
    try:
        with _read_engine() as engine, Session(engine) as session:
            ranking = peer_ranking(session, _scope_id(session, league, season), role, metric)
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    if ranking is None:
        raise _fail(SyncError(f"no {role} peer group in {season}"))
    group = ranking.group
    unit = BY_CODE[metric].unit
    model = " (model)" if ranking.is_model_output else ""
    direction = {"higher": "higher is better", "lower": "lower is better", "none": "style"}
    basis = "per 90" if ranking.basis == "per90" else "rate"
    typer.echo(f"{metric}{model} · {basis} · {direction.get(ranking.direction, '')}")
    typer.echo(
        f"{group.peer_role}s with ≥ {group.min_minutes} min: {group.size} players, "
        f"{ranking.ranked_among} ranked on this metric"
    )
    if group.size < SMALL_GROUP:
        typer.secho(
            f"small peer group (< {SMALL_GROUP}): percentiles are less reliable", fg="yellow"
        )
    typer.echo(f"  {'#':>3}  {'player':<26} {'team':<22} {'minutes':>9}  {'value':>8}  {'pct':>5}")

    def show(rows: list[PeerRow], start: int) -> None:
        for i, row in enumerate(rows, start=start):
            typer.echo(
                f"  {i:>3}  {row.name:<26} {row.team:<22} {row.minutes:>5} min  "
                f"{_format_ranked(row.value, ranking.basis, unit):>8}  {row.pct:>5.1f}"
            )

    rows = ranking.rows
    show(rows[:top], 1)
    tail_start = max(top, len(rows) - bottom)
    if bottom and tail_start < len(rows):
        if tail_start > top:
            typer.echo("  ...")
        show(rows[tail_start:], tail_start + 1)


@report_app.command("thresholds")
def report_thresholds(
    league: Annotated[str, typer.Option(help="PitchAPI league ID, e.g. l_4WFCIZ.")],
    season: Annotated[str, typer.Option(help='Season label, e.g. "2025/2026".')],
) -> None:
    """For each success-rate metric: per peer group, players ranked / players with data.
    The gap is who the minimum attempts leave out; tune them in metrics/registry.py."""
    try:
        with _read_engine() as engine, Session(engine) as session:
            groups, rows = threshold_report(session, _scope_id(session, league, season))
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    if not groups:
        raise _fail(SyncError("no peer groups yet: run `scouting aggregate`"))
    roles = [g.peer_role for g in groups]
    typer.echo(
        "ranked / attempted at least once ('-' = nobody in the group attempted it), "
        f"per peer group (≥ {groups[0].min_minutes} min) · sizes: "
        + " · ".join(f"{g.peer_role} {g.size}" for g in groups)
    )
    typer.echo(f"  {'metric':<24} {'min':>5}  " + "".join(f"{r:>9}" for r in roles))
    for row in rows:
        cells = []
        for r in roles:
            ranked, with_data = row.counts.get(r, (0, 0))
            cell = "-" if with_data == 0 else f"{ranked}/{with_data}"
            cells.append(f"{cell:>9}")
        typer.echo(f"  {row.code:<24} {row.min_attempts:>5}  " + "".join(cells))


# --- midfield depth (slice 2d-2, 2d-2b) ----------------------------------------------------------

DEPTH_BIN: Final = 1 / PITCH_LENGTH_M  # histogram bin: 1 m
NEAREST_TO_CUT: Final = 10


def _m(value: float | None) -> str:
    """A relative depth (share of the pitch length) in metres."""
    return "-" if value is None else f"{value * PITCH_LENGTH_M:+.1f} m"


def _deepest(p: PlayerLine) -> str:
    d = p.depth
    if d is None or d.deepest_share is None:
        return "-"
    return f"{d.deepest_share:.0%} of {d.comparable}"


_TABLE_HEADER: Final = (
    f"    {'player':<26} {'team':<24} {'CBs':>9} {'outfield':>9} {'starts':>6} "
    f"{'deepest':>11} {'partner':>9}  label  group"
)


def _stored(p: PlayerLine) -> str:
    """The stored group, with how it was decided when depth alone didn't."""
    if p.stored is None:
        return "-"
    return p.stored if p.method in (None, "depth") else f"{p.stored} ({p.method})"


def _row(p: PlayerLine, cut: float) -> str:
    d = p.depth
    assert d is not None and d.relative is not None
    return (
        f"    {p.name:<26} {p.team:<24} {_m(d.relative):>9} {_m(p.outfield):>9} "
        f"{d.starts:>6} {_deepest(p):>11} {_m(d.partner_gap):>9}  "
        f"{label_for(d.relative, cut):<5}  {_stored(p)}"
    )


def _print_depth(c: Calibration, show_all: bool) -> None:
    typer.echo(
        f"reference: {REFERENCE_LABELS[depth_rules.DEPTH_REFERENCE]} · cut {_m(c.cut)} in "
        "front of them (below = 6, at or above = 8)"
    )
    typer.echo(
        "columns: CBs = metres in front of his centre-backs (decides) · outfield = vs the "
        "outfield players (second opinion) · deepest = how often he was his team's deepest "
        "central mid · partner = how much deeper his deepest partner played (+ = a deeper "
        "partner, - = he was the deeper one) · label = by depth alone at this cut · "
        "group = stored by `scouting aggregate` (with how, if not by depth)"
    )
    values = [_relative_of(p) for p in c.classified]
    typer.echo(
        f"  {len(c.group)} central midfielders (DM + CM peer groups, ≥ {c.min_minutes} min); "
        f"{len(values)} with ≥ {depth_rules.MIN_DEPTH_STARTS} central-mid starts with a heatmap"
    )
    typer.echo("  histogram (1 m bins, labelled by where each starts):")
    for start, count in histogram(values, DEPTH_BIN):
        marker = f"  ◄ cut {_m(c.cut)}" if start <= c.cut < start + DEPTH_BIN else ""
        typer.echo(f"    {start * PITCH_LENGTH_M:>+5.0f} m {'#' * count:<30} {count:>3}{marker}")

    typer.echo("  anchors (your calls, provisional; label = by depth alone):")
    typer.echo(_TABLE_HEADER)
    for a in c.anchors:
        call = "6" if a.label == "DM" else "8"
        if a.status != "ok" or a.player is None:
            name = a.player.name if a.player else a.query
            typer.echo(f"    {call}  {name:<26} not used: {a.status}")
            continue
        agrees = label_for(_relative_of(a.player), c.cut) == a.label
        mark = "✓" if agrees else "✗ other side of the cut"
        typer.echo(f"  {call}{_row(a.player, c.cut)[3:]}  {mark}")
    if c.suggested is not None:
        wrong = ", ".join(c.suggested.wrong) or "none"
        typer.echo(
            f"  the anchors alone would cut at {_m(c.suggested.value)} (margin "
            f"{c.suggested.margin * PITCH_LENGTH_M:.1f} m; wrong side: {wrong})"
        )
    dm, cm, fallback = c.sizes()
    typer.echo(
        f"  with the {_m(c.cut)} cut, depth alone: DM {dm} · CM {cm} · "
        f"formation fallback {fallback}"
    )
    roles, methods = c.stored_counts()
    typer.echo(
        f"  stored groups: DM {roles['DM']} · CM {roles['CM']} (by depth {methods['depth']} · "
        f"deeper partner {methods['partner']} · formation {methods['formation']})"
    )
    if abs(c.cut - depth_rules.DEPTH_CUT) > 1e-9:
        typer.echo("  (a trial cut: the stored groups use the agreed one and aren't compared)")
    elif mismatched := c.mismatches():
        names = ", ".join(p.name for p in mismatched[:10])
        typer.secho(
            f"  {len(mismatched)} stored groups differ from the rule ({names}): "
            "run `scouting aggregate`",
            fg="yellow",
        )
    else:
        typer.echo("  stored groups match the rule")

    typer.echo(f"  closest to the cut ({NEAREST_TO_CUT}):")
    typer.echo(_TABLE_HEADER)
    for p in c.nearest(NEAREST_TO_CUT):
        typer.echo(_row(p, c.cut))
    disagree = c.disagreements()
    typer.echo(
        f"  cross-check: {len(disagree)} labelled 6 but rarely the deepest midfielder "
        f"(≤ 30%), or 8 but usually the deepest (≥ 70%)"
    )
    for p in disagree:
        typer.echo(_row(p, c.cut))

    typer.echo(
        "  deeper-partner rule: a 6 by depth whose deepest partner played on average at "
        f"least G deeper (over ≥ {depth_rules.MIN_PARTNER_STARTS} starts beside a partner) "
        "becomes an 8"
    )
    typer.echo(f"    {'G':>5} {'flips':>5} {'DM':>4} {'CM':>4}  who (partner gap)")
    for gap_m in depth_rules.PARTNER_GAP_CANDIDATES_M:
        gap = gap_m / PITCH_LENGTH_M
        flips = c.partner_flips(gap)
        dm, cm, _ = c.sizes(gap)
        who = ", ".join(f"{p.name} ({_m(p.depth.partner_gap if p.depth else None)})" for p in flips)
        agreed = "  ◄ agreed" if gap_m == depth_rules.PARTNER_GAP_M else ""
        typer.echo(f"    {gap_m:>3g} m {len(flips):>5} {dm:>4} {cm:>4}  {who or '-'}{agreed}")
    typer.echo("  6s by depth, largest deeper-partner gap first:")
    typer.echo(_TABLE_HEADER)
    for p in c.sixes():
        typer.echo(_row(p, c.cut))

    if show_all:
        typer.echo("  everyone, deepest first:")
        typer.echo(_TABLE_HEADER)
        for p in c.classified:
            typer.echo(_row(p, c.cut))
        for p in c.group:
            if p not in c.classified:
                starts = 0 if p.depth is None else p.depth.starts
                typer.echo(
                    f"    {p.name:<26} {p.team:<24} formation fallback ({starts} starts): "
                    f"{_stored(p)}"
                )


def _relative_of(p: PlayerLine) -> float:
    assert p.depth is not None and p.depth.relative is not None
    return p.depth.relative


@report_app.command("depth")
def report_depth(
    league: Annotated[str, typer.Option(help="PitchAPI league ID, e.g. l_4WFCIZ.")],
    season: Annotated[str, typer.Option(help='Season label, e.g. "2025/2026".')],
    cut_m: Annotated[
        float | None,
        typer.Option(
            help="Metres in front of the centre-backs that separate 6s from 8s, to try "
            "another cut (default: the agreed 14)."
        ),
    ] = None,
    show_all: Annotated[
        bool, typer.Option("--all", help="Also list every central midfielder.")
    ] = False,
) -> None:
    """The 6/8 split for central midfielders (slices 2d-2, 2d-2b): season depth in front of
    their centre-backs, the anchors, the cut, the cross-check and the deeper-partner rule at
    a few candidate gaps, and whether the stored groups match the rule (D-018). Read-only."""
    try:
        with _read_engine() as engine, Session(engine) as session:
            season_id = find_season(session, league, season).id
            result = calibrate(
                session, season_id, cut=None if cut_m is None else cut_m / PITCH_LENGTH_M
            )
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    typer.echo(f"midfield depth · {league} {season}")
    _print_depth(result, show_all)


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


# --- aggregates (slice 2a) -----------------------------------------------------------------


def _aggregate(engine: Engine, league: str, season: str) -> AggregateSummary:
    with Session(engine) as session, session.begin():
        # Refresh the metric definitions first: a registry change (e.g. a tuned minimum in
        # MIN_ATTEMPTS) then takes effect on the next aggregate, with no match re-sync.
        sync_reference_data(session)
        return aggregate_season(session, find_season(session, league, season).id)


def _print_aggregate(summary: AggregateSummary) -> None:
    typer.echo(
        f"aggregated {summary.season}: {summary.players} players · "
        f"{summary.stat_rows} season stat rows"
    )
    sizes = " · ".join(
        f"{role} {summary.peer_roles[role]}" for role in PEER_ROLES if summary.peer_roles[role]
    )
    no_role = summary.peer_roles[None]
    typer.echo(
        f"  peer groups (≥ {summary.min_minutes} min): {sizes}"
        + (f" · no role {no_role}" if no_role else "")
    )
    if summary.peer_methods:
        methods = summary.peer_methods
        typer.echo(
            f"  6/8 split (central mids ≥ {summary.min_minutes} min): by depth "
            f"{methods['depth']} · deeper partner {methods['partner']} · "
            f"formation {methods['formation']}"
        )
    sources = summary.role_sources
    typer.echo(
        f"  roles: {sources['starts']} from starts, {sources['listed']} from listed position, "
        f"{sources[None]} none"
    )
    if summary.percentiles is not None:
        typer.echo(
            f"  percentiles: {summary.percentiles.rows} across "
            f"{len(summary.percentiles.group_sizes)} peer groups"
        )
        for role in summary.percentiles.small_groups:
            size = summary.percentiles.group_sizes[role]
            typer.secho(
                f"  small peer group: {role} has {size} players (< {SMALL_GROUP}); "
                "its percentiles are less reliable",
                fg="yellow",
            )


@app.command("aggregate")
def aggregate_command(
    league: Annotated[str, typer.Option(help="PitchAPI league ID, e.g. l_4WFCIZ.")],
    season: Annotated[str, typer.Option(help='Season label, e.g. "2025/2026".')],
) -> None:
    """Rebuild a season's totals, per-90s, rates and roles from the stored matches.
    Runs automatically after a complete `sync season`; reads scouting.db only."""
    try:
        with _read_engine() as engine:
            summary = _aggregate(engine, league, season)
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    _print_aggregate(summary)


def _season_value(line: SeasonStatLine) -> tuple[str, str]:
    """(total column, per 90 / rate column) for the season table."""
    if line.kind == "fraction":
        if line.value_is_rate:  # the stored total is rate x weight: only the rate means much
            unit = "%" if line.unit == "rate" else f" {line.unit}"
            rate = "n/a" if line.rate is None else f"{line.rate:.4g}{unit}"
            return f"weight {_format_number(line.denominator)}", rate
        total = f"{_format_number(line.total)} / {_format_number(line.denominator)}"
        return total, "n/a" if line.rate is None else f"{line.rate * 100:.1f}%"
    per90 = "n/a" if line.per90 is None else f"{line.per90:.4g}"
    return _format_number(line.total), per90


_DIRECTION_MARK: Final = {"higher": "", "lower": " ↓", "none": " ~"}


_ORDINAL_SUFFIX: Final = {1: "st", 2: "nd", 3: "rd"}


def _ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th ... 11th, 12th, 13th ... 21st."""
    suffix = "th" if n % 100 in (11, 12, 13) else _ORDINAL_SUFFIX.get(n % 10, "th")
    return f"{n}{suffix}"


def _pct_cell(line: SeasonStatLine, detail: PlayerSeason) -> str:
    """The percentile, or why there isn't one. "85th (n=57)": 85th percentile among the 57
    players ranked on this metric (not "85th of 57")."""
    if detail.group is None:
        return ""
    if line.pct is not None:
        return f"{_ordinal(round(line.pct))} (n={line.ranked_among})"
    if line.kind == "fraction":
        if line.min_attempts is not None and (line.denominator or 0) < line.min_attempts:
            return f"< {line.min_attempts} att"
        return "n/a"
    if line.minutes < detail.group.min_minutes:
        return f"< {detail.group.min_minutes} min"
    return "n/a"


def _metres(share: float) -> str:
    return f"{share * PITCH_LENGTH_M:.1f} m"


def _six_or_eight(detail: PlayerSeason) -> str | None:
    """How a central mid's 6/8 group was decided (D-018); None for everyone else."""
    if detail.peer_method is None:
        return None
    call = "a 6 (DM)" if detail.peer_role == "DM" else "an 8 (CM)"
    depth = (
        None
        if detail.depth is None
        else f"{_metres(detail.depth)} in front of his centre-backs "
        f"over {detail.depth_starts} starts"
    )
    if detail.peer_method == "formation":
        text = (
            f"{call} by formation: {detail.depth_starts or 0} central-mid starts with a heatmap "
            f"(needs {depth_rules.MIN_DEPTH_STARTS})"
        )
        return text + (f"; {depth}" if depth else "")
    if detail.peer_method == "partner" and detail.partner_gap is not None:
        return (
            f"{call} beside a deeper partner: {depth}, but his deepest fellow central mid "
            f"played {_metres(detail.partner_gap)} deeper over {detail.partner_starts} starts "
            f"(≥ {depth_rules.PARTNER_GAP_M:g} m makes an 8)"
        )
    text = f"{call} by depth: {depth} (cut {depth_rules.DEPTH_CUT * PITCH_LENGTH_M:.3g} m)"
    if detail.depth is not None and depth_rules.is_borderline(detail.depth):
        text += f" · borderline (within {depth_rules.BORDERLINE_M:g} m of the cut)"
    return text


def _print_player_season(detail: PlayerSeason) -> None:
    typer.echo(f"{detail.name} ({detail.external_id}) · {detail.competition} {detail.season}")
    teams = ", ".join(f"{name} {minutes} min" for name, minutes, _ in detail.teams)
    typer.echo(
        f"  {detail.minutes} min · {detail.matches} matches · {detail.starts} starts · {teams}"
    )
    if detail.peer_role is None:
        role = "no role (no starts, and the listed position doesn't decide one)"
    else:
        via = "most minutes as a starter" if detail.role_source == "starts" else "listed position"
        role = f"{detail.primary_role or '?'} (from {via}) · peer group {detail.peer_role}"
    typer.echo(f"  role: {role}")
    split = _six_or_eight(detail)
    if split is not None:
        typer.echo(f"  6 or 8: {split}")
    if detail.roles:
        breakdown = ", ".join(f"{r} {m} min / {n} starts" for r, m, n in detail.roles)
        typer.echo(f"    as a starter: {breakdown}")
    rating = "not rated" if detail.avg_rating is None else f"{detail.avg_rating:.2f}"
    typer.echo(f"  rating: {rating} ({detail.rated_matches} rated matches)")
    if detail.group is not None:
        group = detail.group
        small = " · small group" if group.size < SMALL_GROUP else ""
        typer.echo(
            f"  percentiles vs {group.size} {group.peer_role}s (≥ {group.min_minutes} min){small}"
            " · raw: ↓ = lower is better, ~ = style, no direction"
        )
    elif detail.group_min_minutes is not None:
        why = (
            f"below {detail.group_min_minutes} min"
            if detail.peer_role is not None
            else "no peer role"
        )
        typer.echo(f"  percentiles: not ranked ({why}); a comparison can still rank him by hand")
    typer.echo("")
    typer.echo(
        f"  {'metric':<28} {'total':<16} {'per 90 / rate':<14} {'minutes':>7} {'matches':>7}"
        f"  {'percentile':<14}"
    )
    for line in detail.stats:
        total, value = _season_value(line)
        name = (
            f"{line.code}{' (model)' if line.is_model_output else ''}"
            f"{_DIRECTION_MARK.get(line.direction, '')}"
        )
        typer.echo(
            f"  {name:<28} {total:<16} {value:<14} {line.minutes:>7} {line.matches:>7}"
            f"  {_pct_cell(line, detail):<14}"
        )


@inspect_app.command("player")
def inspect_player(
    player: Annotated[
        str, typer.Argument(help="PitchAPI player ID (p_...) or part of a name, e.g. salah.")
    ],
    league: Annotated[str, typer.Option(help="PitchAPI league ID, e.g. l_4WFCIZ.")],
    season: Annotated[str, typer.Option(help='Season label, e.g. "2025/2026".')],
    metric: Annotated[
        list[str] | None,
        typer.Option(help="Only these metric codes (repeatable), e.g. --metric goals."),
    ] = None,
    matches: Annotated[
        bool, typer.Option(help="With one --metric: list every match's stored value.")
    ] = False,
) -> None:
    """A player's season: minutes, role, rating, and every metric's total, per 90 and rate."""
    unknown = [code for code in metric or [] if code not in BY_CODE]
    if unknown:
        raise _fail(SyncError(f"unknown metric code(s): {', '.join(unknown)}"))
    if matches and len(metric or []) != 1:
        raise _fail(SyncError("--matches needs exactly one --metric"))
    try:
        with _read_engine() as engine, Session(engine) as session:
            season_row = find_season(session, league, season)
            scope = session.scalars(select(Scope).where(Scope.season_id == season_row.id)).first()
            if scope is None:
                raise SyncError(f"{season} isn't aggregated yet: run `scouting aggregate`")
            found = find_players(session, scope.id, player)
            if not found:
                raise SyncError(f"no player matching {player!r} in {season}")
            if len(found) > 1 and not any(p.external_id == player for p in found):
                typer.echo(f"{len(found)} players match {player!r}; use a longer name or the ID:")
                for p in found[:15]:
                    typer.echo(f"  {p.external_id}  {p.name} ({p.minutes} min)")
                raise typer.Exit(code=1)
            detail = player_season(session, scope.id, found[0].player_id)
            by_match = (
                metric_by_match(session, season_row.id, found[0].player_id, metric[0])
                if matches and metric
                else []
            )
    except EXPECTED_ERRORS as exc:
        raise _fail(exc) from exc
    if detail is None:
        raise _fail(SyncError("no aggregates for this player yet: run `scouting aggregate`"))
    if metric:
        detail = replace(detail, stats=[s for s in detail.stats if s.code in metric])
    _print_player_season(detail)
    if by_match:
        typer.echo("")
        typer.echo(f"  {metric[0] if metric else ''} by match (stored values):")
        for row in by_match:
            value = _format_number(row.value)
            if row.denominator is not None:
                value += f" / {_format_number(row.denominator)}"
            typer.echo(f"    {row.match_date}  {row.match_label:<40} {row.minutes:>3} min  {value}")


def main() -> None:  # pragma: no cover - console-script shim
    app()
