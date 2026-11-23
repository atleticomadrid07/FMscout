"""The FastAPI application.

    uv run scouting serve
    uv run uvicorn scouting.api.app:create_app --factory --reload

Reads DATABASE_URL like the CLI, opens it read-only, and checks the schema once at startup.
A database that isn't migrated (or doesn't exist) doesn't stop the server: /health says why
and every data endpoint answers 503 SCHEMA_OUT_OF_DATE until it's fixed and restarted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from scouting.api.deps import read_only_engine
from scouting.api.errors import install_error_handlers, install_openapi
from scouting.api.routes import compare, groups, meta, metrics, players, scopes, templates
from scouting.config.settings import Settings
from scouting.db.schema_version import SchemaOutOfDateError, check_schema_current

PREFIX: Final = "/api/v1"
# The Vite dev server (Phase 4). The API binds to localhost only (D-007: local for now).
DEV_ORIGINS: Final = ("http://localhost:5173", "http://127.0.0.1:5173")


def create_app(database_url: str | None = None) -> FastAPI:
    url = database_url if database_url is not None else Settings().database_url

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = read_only_engine(url)
        app.state.schema_problem = None
        app.state.search_cache = {}
        try:
            check_schema_current(app.state.engine)
        except (SchemaOutOfDateError, SQLAlchemyError) as exc:
            app.state.schema_problem = str(exc).splitlines()[0]
        try:
            yield
        finally:
            app.state.engine.dispose()

    app = FastAPI(
        title="Scouting API",
        version=meta.API_VERSION,
        summary="Read-only season stats, percentiles and position templates (PitchAPI data).",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=list(DEV_ORIGINS), allow_methods=["GET"], allow_headers=["*"]
    )
    install_error_handlers(app)
    routers = (meta, scopes, metrics, templates, players, groups, compare)
    for router in (module.router for module in routers):
        app.include_router(router, prefix=PREFIX)
    install_openapi(app)
    return app
