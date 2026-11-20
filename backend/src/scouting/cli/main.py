"""`scouting` command-line interface (Typer). Run from backend/: `uv run scouting --help`.

Only this CLI talks to PitchAPI (D-004); the web app reads scouting.db only.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.cache import CachedFetcher, CacheMissError, FetchStats
from scouting.adapters.pitchapi.client import PitchApiClient
from scouting.adapters.pitchapi.errors import PitchApiError
from scouting.adapters.pitchapi.raw_store import RawStore, RawStoreError
from scouting.config.settings import MissingApiKeyError, Settings
from scouting.db.base import make_engine
from scouting.db.schema_version import SchemaOutOfDateError, check_schema_current
from scouting.ingest.match_list import MatchListSummary, SyncError, UpsertCounts, sync_match_list

app = typer.Typer(no_args_is_help=True, help="FM Scout backend commands.")
sync_app = typer.Typer(no_args_is_help=True, help="Fetch PitchAPI data into the local databases.")
app.add_typer(sync_app, name="sync")

# Errors we expect and can explain in one line; anything else gets a full traceback.
EXPECTED_ERRORS = (
    MissingApiKeyError,
    SchemaOutOfDateError,
    SyncError,
    CacheMissError,
    RawStoreError,
    PitchApiError,
)


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _counts(label: str, counts: UpsertCounts) -> str:
    return (
        f"  {label:<8} {counts.total:>4} total: {counts.inserted} new, "
        f"{counts.updated} updated, {counts.unchanged} unchanged"
    )


def _print_summary(summary: MatchListSummary, stats: FetchStats, *, offline: bool) -> None:
    kind = "current season" if summary.is_current else "completed season"
    source = "cache" if summary.match_list_from_cache else "PitchAPI"
    typer.echo(f"{summary.competition} {summary.season} ({kind}; match list from {source})")
    typer.echo(_counts("teams", summary.teams))
    typer.echo(_counts("matches", summary.matches))
    mode = " (offline)" if offline else ""
    typer.echo(
        f"  requests: {stats.network_fetches} to PitchAPI, {stats.cache_hits} from cache{mode}"
    )


@sync_app.command("matches")
def sync_matches(
    league: Annotated[
        str, typer.Option(help="PitchAPI league ID, e.g. l_4WFCIZ (Premier League).")
    ],
    season: Annotated[
        str, typer.Option(help='Season label as PitchAPI lists it, e.g. "2025/2026".')
    ],
    offline: Annotated[
        bool, typer.Option(help="Use cached responses only; never call PitchAPI.")
    ] = False,
) -> None:
    """Sync one league-season's competition, teams and match list into scouting.db."""
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
                summary = sync_match_list(fetcher, session, league, season)
    except EXPECTED_ERRORS as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()
    _print_summary(summary, fetcher.stats, offline=offline)


def main() -> None:  # pragma: no cover - console-script shim
    app()
