"""SQLAlchemy foundations for scouting.db: declarative base and engine factory.

The naming convention gives every constraint a predictable name. Alembic needs that
to alter or drop constraints on SQLite (batch mode rebuilds tables and has to
recreate constraints by name), and it keeps migrations portable to Postgres.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: Final = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    # SQLite ignores foreign keys unless this is set on every connection.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(url: str) -> Engine:
    """Create an engine; for SQLite, turn on foreign keys and create the file's folder."""
    parsed = make_url(url)
    engine = create_engine(url)
    if parsed.get_backend_name() == "sqlite":
        database = parsed.database
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine
