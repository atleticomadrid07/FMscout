"""Liveness and the database's state."""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError

from scouting.api.schemas import Health
from scouting.db.schema_version import current_revision

API_VERSION = "1"

router = APIRouter(tags=["meta"])


@router.get("/health")
def health(request: Request) -> Health:
    """Always answers 200; `status` says whether the database is usable."""
    problem: str | None = request.app.state.schema_problem
    try:
        revision = current_revision(request.app.state.engine)
    except SQLAlchemyError:  # a missing or unreadable file: the problem text says which
        revision = None
    return Health(
        status="ok" if problem is None else "schema_out_of_date",
        api_version=API_VERSION,
        schema_revision=revision,
        message=problem,
    )
