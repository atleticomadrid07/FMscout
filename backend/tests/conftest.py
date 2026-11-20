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


# --- a three-match fixture season (slice 1d) -----------------------------------------------
#
# Built from the hand-made m_FIXT01 data so all three fixture matches have full per-match
# data. Each side of m_FIXT01 is a template: T1 = the home side (4-2-3-1, 5 players incl. an
# unused sub), T2 = the away side (3-4-2-1, 3 players incl. a sub keeper). Player IDs are
# rewritten per team ("p_FXGK01" → "p_AGK01" for team A), so a team keeps its players
# across matches. Still hand-made fixture data, not real PitchAPI data.

SeasonResponses = dict[str, Any]
SEASON_MATCHES = (  # match, (home team, template, letter), (away team, template, letter)
    ("m_FIXT01", ("t_FIXTA1", "T1", "A"), ("t_FIXTB2", "T2", "B")),
    ("m_FIXT02", ("t_FIXTB2", "T2", "B"), ("t_FIXTC3", "T1", "C")),
    ("m_FIXT03", ("t_FIXTC3", "T1", "C"), ("t_FIXTA1", "T2", "A")),
)
_TEMPLATES = {"T1": ("t_FIXTA1", "home"), "T2": ("t_FIXTB2", "away")}


@pytest.fixture
def season_responses(load_fixture: LoadFixture) -> SeasonResponses:
    """API path → `data` payload for a whole fixture season, profiles included."""
    players = load_fixture("match_players.json")
    advanced = load_fixture("match_advanced.json")["players"]
    lineups = load_fixture("match_lineups.json")

    def side(template: str, team: str, letter: str) -> tuple[Any, Any, Any]:
        source_team, lineup_side = _TEMPLATES[template]
        picked = (
            [r for r in players if r["team_id"] == source_team],
            [r for r in advanced if r["team_id"] == source_team],
            lineups[lineup_side],
        )
        text = json.dumps(picked).replace("p_FX", f"p_{letter}").replace(source_team, team)
        loaded: tuple[Any, Any, Any] = tuple(json.loads(text))
        return loaded

    responses: SeasonResponses = {
        "/v1/leagues": load_fixture("leagues.json"),
        "/v1/leagues/l_FIXT01/matches": load_fixture("match_list.json"),
    }
    names: dict[str, str] = {}
    for match_id, home, away in SEASON_MATCHES:
        h_rows, h_adv, h_side = side(home[1], home[0], home[2])
        a_rows, a_adv, a_side = side(away[1], away[0], away[2])
        responses[f"/v1/matches/{match_id}/players"] = h_rows + a_rows
        responses[f"/v1/matches/{match_id}/advanced/players"] = {"players": h_adv + a_adv}
        responses[f"/v1/matches/{match_id}/lineups"] = {"home": h_side, "away": a_side}
        names.update({r["player"]["id"]: r["player"]["name"] for r in h_rows + a_rows})
    for player_id, name in names.items():
        first, _, last = name.partition(" ")
        responses[f"/v1/players/{player_id}"] = {
            "id": player_id,
            "name": name,
            "short_name": f"{first[0]}. {last}",
            "shirt_number": "7",
            "position_id": 11 if "GK" in player_id else 2,
            "country_code": "ENG",
            "image_url": None,
        }
    return responses
