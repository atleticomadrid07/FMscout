"""Shared pytest fixtures. Loaded automatically by pytest for every test under tests/."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select

from scouting.db import models  # noqa: F401  (registers every table on Base.metadata)
from scouting.db.base import Base, make_engine

PITCHAPI_FIXTURES = Path(__file__).parent / "fixtures" / "pitchapi"

LoadFixture = Callable[[str], Any]
Snapshot = Callable[[Engine], dict[str, list[tuple[Any, ...]]]]


@pytest.fixture
def load_fixture() -> LoadFixture:
    """Return the `data` payload of a hand-made fixture in tests/fixtures/pitchapi/."""

    def load(name: str) -> Any:
        return json.loads((PITCHAPI_FIXTURES / name).read_text(encoding="utf-8"))["data"]

    return load


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    """A fresh scouting.db built straight from the models (fast; no migrations).
    tests/unit/db/test_migrations.py checks that the migrations build the same schema."""
    engine = make_engine(f"sqlite:///{tmp_path / 'scouting.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def snapshot() -> Snapshot:
    """Every row of every table, sorted: two equal snapshots mean identical databases."""

    def take(engine: Engine) -> dict[str, list[tuple[Any, ...]]]:
        with engine.connect() as conn:
            return {
                table.name: sorted(tuple(row) for row in conn.execute(select(table)))
                for table in Base.metadata.sorted_tables
            }

    return take
