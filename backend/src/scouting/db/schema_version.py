"""Compare scouting.db's Alembic revision with the latest migration in the repo.

The CLI calls `check_schema_current()` before writing, so running a sync against an
outdated database fails with "run alembic upgrade head" instead of a cryptic SQL error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

# src/scouting/db/schema_version.py → parents[3] is backend/ (editable install, D-008).
BACKEND_DIR: Final = Path(__file__).resolve().parents[3]
ALEMBIC_INI: Final = BACKEND_DIR / "alembic.ini"


class SchemaOutOfDateError(RuntimeError):
    """scouting.db isn't at the latest migration."""


def alembic_config(database_url: str | None = None) -> Config:
    """Alembic config pointing at backend/alembic.ini, optionally with an explicit URL."""
    config = Config(str(ALEMBIC_INI))
    if database_url is not None:
        # ConfigParser treats % as interpolation; escape it so URLs pass through unchanged.
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def check_schema_current(engine: Engine) -> None:
    current, head = current_revision(engine), head_revision()
    if head is None:
        raise SchemaOutOfDateError(
            "No migrations exist yet. Run: uv run alembic revision --autogenerate "
            '-m "match list tables", then: uv run alembic upgrade head'
        )
    if current != head:
        raise SchemaOutOfDateError(
            f"scouting.db is at revision {current or '(empty)'}, the code expects {head}. "
            "Run: uv run alembic upgrade head"
        )
