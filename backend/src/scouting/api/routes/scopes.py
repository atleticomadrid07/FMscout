"""Seasons available, with coverage and peer groups."""

from __future__ import annotations

from fastapi import APIRouter

from scouting.api.deps import SessionDep
from scouting.api.errors import ERROR_RESPONSES, not_found
from scouting.api.schemas import CoverageOut, PeerGroupOut, ScopeOut, ScopesOut
from scouting.db.queries import ScopeSummary, find_scope, scope_summaries
from scouting.metrics.percentiles import SMALL_GROUP

router = APIRouter(tags=["scopes"], responses=ERROR_RESPONSES)


def scope_out(summary: ScopeSummary) -> ScopeOut:
    c = summary.coverage
    return ScopeOut(
        key=summary.key,
        kind="season",
        source="pitchapi",
        competition=summary.competition,
        country_code=summary.country_code,
        season=summary.season,
        is_current=summary.is_current,
        aggregated=bool(summary.groups),
        min_minutes=summary.min_minutes,
        computed_at=summary.computed_at,
        coverage=CoverageOut(
            matches=c.matches,
            players_missing=c.players_missing,
            advanced_unavailable=c.advanced_unavailable,
            lineups_missing=c.lineups_missing,
            heatmaps_missing=c.heatmaps_missing,
            pending=c.pending,
            matches_with_ratings=c.matches_with_ratings,
        ),
        peer_groups=[
            PeerGroupOut(
                role=g.peer_role,
                size=g.size,
                min_minutes=g.min_minutes,
                small_sample=g.size < SMALL_GROUP,
            )
            for g in summary.groups
        ],
    )


@router.get("/scopes")
def list_scopes(session: SessionDep) -> ScopesOut:
    """Every season in the database, newest first. Use a scope's `key` in other requests."""
    return ScopesOut(scopes=[scope_out(s) for s in scope_summaries(session)])


@router.get("/scopes/{key}")
def get_scope(key: str, session: SessionDep) -> ScopeOut:
    summary = find_scope(session, key)
    if summary is None:
        raise not_found("SCOPE_NOT_FOUND", f"no season with key {key!r}; see /api/v1/scopes")
    return scope_out(summary)
