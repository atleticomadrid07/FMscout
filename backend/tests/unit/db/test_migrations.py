"""The Alembic migrations must build exactly the schema the models describe.

If a model changes without a matching migration (or the other way round), this fails.
Skipped until the first migration exists in migrations/versions/.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from scouting.db.base import Base, make_engine
from scouting.db.schema_version import (
    SchemaOutOfDateError,
    alembic_config,
    check_schema_current,
    head_revision,
)

pytestmark = pytest.mark.skipif(
    head_revision() is None,
    reason="no migrations yet: run `uv run alembic revision --autogenerate -m ...` first",
)


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'migrated.db'}"


def test_upgrade_head_matches_models(tmp_path: Path) -> None:
    url = _url(tmp_path)
    command.upgrade(alembic_config(url), "head")
    engine = make_engine(url)
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()
    assert diff == [], f"models and migrations differ: {diff}"


def test_downgrade_to_base_removes_all_tables(tmp_path: Path) -> None:
    url = _url(tmp_path)
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = make_engine(url)
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()


def test_check_schema_current_passes_after_upgrade(tmp_path: Path) -> None:
    url = _url(tmp_path)
    command.upgrade(alembic_config(url), "head")
    engine = make_engine(url)
    check_schema_current(engine)
    engine.dispose()


def test_check_schema_current_fails_on_empty_database(tmp_path: Path) -> None:
    engine = make_engine(_url(tmp_path))
    with pytest.raises(SchemaOutOfDateError, match="alembic upgrade head"):
        check_schema_current(engine)
    engine.dispose()


def test_status_columns_default_to_pending_in_the_database(tmp_path: Path) -> None:
    """Server defaults: a plain-SQL insert without status columns still works."""
    url = _url(tmp_path)
    command.upgrade(alembic_config(url), "head")
    engine = make_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO competition (id, name, country_code) VALUES (1, 'C', 'ENG')"
        )
        conn.exec_driver_sql(
            "INSERT INTO season (id, competition_id, label, is_current) VALUES (1, 1, '2025', 1)"
        )
        conn.exec_driver_sql("INSERT INTO team (id, name) VALUES (1, 'A'), (2, 'B')")
        conn.exec_driver_sql(
            "INSERT INTO match (season_id, kickoff_utc, match_date, status, home_team_id, "
            "away_team_id) VALUES (1, '2025-08-15 19:00:00', '2025-08-15', 'finished', 1, 2)"
        )
        row = conn.exec_driver_sql(
            "SELECT players_status, advanced_status, lineups_status FROM match"
        ).one()
    engine.dispose()
    assert tuple(row) == ("pending", "pending", "pending")


def test_dm_peer_role_migration_round_trip(tmp_path: Path) -> None:
    """2d-3 (D-018): the derived peer tables come back empty, with DM allowed; downgrading
    restores the 7-role CHECK. Uses sqlite3 directly so foreign keys stay off for the
    bare inserts."""
    url = _url(tmp_path)
    config = alembic_config(url)
    path = tmp_path / "migrated.db"
    insert = (
        "INSERT INTO peer_group (scope_id, peer_role, min_minutes, size, computed_at) "
        "VALUES (1, ?, 0, 0, '2026-11-21 00:00:00')"
    )
    command.upgrade(config, "head")
    command.downgrade(config, "-1")
    db = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(insert, ("DM",))
    db.execute(insert, ("CM",))
    db.commit()
    db.close()

    command.upgrade(config, "head")
    db = sqlite3.connect(path)
    assert db.execute("SELECT count(*) FROM peer_group").fetchone() == (0,)
    db.execute(insert, ("DM",))
    columns = {row[1] for row in db.execute("PRAGMA table_info(player_scope)")}
    assert {"peer_method", "depth", "depth_starts", "partner_gap", "partner_starts"} <= columns
    db.close()
