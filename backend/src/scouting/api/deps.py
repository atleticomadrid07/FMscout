"""The read-only database connection and the per-request session."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from scouting.api.errors import ApiError
from scouting.api.search import PlayerIndex
from scouting.db.base import make_engine
from scouting.db.queries import (
    PlayerSeason,
    ScopeSummary,
    find_scope,
    player_id_for,
    player_season,
    search_entries,
)


def read_only_url(database_url: str) -> str:
    """For a SQLite file, open it read-only (`mode=ro`): the API can't write scouting.db
    even by mistake. Other backends are returned unchanged."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        return database_url
    path = Path(url.database).resolve()
    return f"sqlite:///file:{path}?mode=ro&uri=true"


def read_only_engine(database_url: str) -> Engine:
    """A read-only SQLite file goes straight to create_engine: make_engine would try to
    create the folder of the "file:" URI path. Foreign-key enforcement doesn't matter when
    nothing can be written."""
    url = read_only_url(database_url)
    return create_engine(url) if url != database_url else make_engine(url)


def get_session(request: Request) -> Iterator[Session]:
    problem: str | None = request.app.state.schema_problem
    if problem is not None:
        raise ApiError(503, "SCHEMA_OUT_OF_DATE", problem)
    with Session(request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


# --- resolving a request's scope and player (slice 3b) -------------------------------------------


def aggregated_scope(session: Session, key: str) -> ScopeSummary:
    """The scope for a `scope=` key, aggregated; else a 404 or 409-style error."""
    summary = find_scope(session, key)
    if summary is None:
        raise ApiError(404, "SCOPE_NOT_FOUND", f"no season with key {key!r}; see /api/v1/scopes")
    if summary.scope_id is None:
        raise ApiError(
            409, "SCOPE_NOT_AGGREGATED", f"{key} has no season stats yet: run `scouting aggregate`"
        )
    return summary


def player_in_scope(
    session: Session, scope: ScopeSummary, external_id: str
) -> tuple[int, PlayerSeason]:
    """(internal player id, his season) for a PitchAPI player id; else a 404."""
    player_id = player_id_for(session, external_id)
    if player_id is None:
        raise ApiError(404, "PLAYER_NOT_FOUND", f"no player {external_id!r}")
    assert scope.scope_id is not None  # aggregated_scope checked it
    season = player_season(session, scope.scope_id, player_id)
    if season is None:
        raise ApiError(404, "PLAYER_NOT_IN_SCOPE", f"{external_id} didn't play in {scope.key}")
    return player_id, season


def search_index(request: Request, session: Session, scope: ScopeSummary) -> PlayerIndex:
    """The scope's search index, built on first use and rebuilt when its aggregates change."""
    cache: dict[str, tuple[datetime | None, PlayerIndex]] = request.app.state.search_cache
    cached = cache.get(scope.key)
    if cached is not None and cached[0] == scope.computed_at:
        return cached[1]
    assert scope.scope_id is not None
    index = PlayerIndex(search_entries(session, scope.scope_id))
    cache[scope.key] = (scope.computed_at, index)
    return index
