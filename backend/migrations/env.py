"""Alembic environment for scouting.db.

The URL comes from alembic.ini's `sqlalchemy.url` when set (tests set it), otherwise
from Settings (DATABASE_URL). `render_as_batch` makes autogenerate write SQLite-safe
migrations: SQLite can't alter or drop columns in place, so batch mode rebuilds the
table instead.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from scouting.config.settings import Settings
from scouting.db import models  # noqa: F401  (imported so every table registers on Base.metadata)
from scouting.db.base import Base, make_engine

config = context.config
if config.config_file_name is not None:
    # Keep loggers the app already configured (CLI, tests) working.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or Settings().database_url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_database_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
