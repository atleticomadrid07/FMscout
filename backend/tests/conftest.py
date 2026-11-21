"""Shared pytest fixtures. Loaded automatically by pytest for every test under tests/."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select

from scouting.adapters.pitchapi.cache import CachedFetcher
from scouting.adapters.pitchapi.client import ApiResponse
from scouting.adapters.pitchapi.raw_store import RawStore
from scouting.db import models  # noqa: F401  (registers every table on Base.metadata)
from scouting.db.base import Base, make_engine
from scouting.ingest.season import sync_season

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
    heatmaps = load_fixture("match_heatmaps.json")  # slice 2d

    def side(template: str, team: str, letter: str) -> tuple[Any, Any, Any, Any]:
        source_team, lineup_side = _TEMPLATES[template]
        picked = (
            [r for r in players if r["team_id"] == source_team],
            [r for r in advanced if r["team_id"] == source_team],
            lineups[lineup_side],
            [r for r in heatmaps["players"] if r["team"]["id"] == source_team],
        )
        text = json.dumps(picked).replace("p_FX", f"p_{letter}").replace(source_team, team)
        loaded: tuple[Any, Any, Any, Any] = tuple(json.loads(text))
        return loaded

    responses: SeasonResponses = {
        "/v1/leagues": load_fixture("leagues.json"),
        "/v1/leagues/l_FIXT01/matches": load_fixture("match_list.json"),
    }
    names: dict[str, str] = {}
    for match_id, home, away in SEASON_MATCHES:
        h_rows, h_adv, h_side, h_heat = side(home[1], home[0], home[2])
        a_rows, a_adv, a_side, a_heat = side(away[1], away[0], away[2])
        responses[f"/v1/matches/{match_id}/players"] = h_rows + a_rows
        responses[f"/v1/matches/{match_id}/advanced/players"] = {"players": h_adv + a_adv}
        responses[f"/v1/matches/{match_id}/lineups"] = {"home": h_side, "away": a_side}
        responses[f"/v1/matches/{match_id}/heatmaps"] = {
            "match_id": match_id,
            "grid": heatmaps["grid"],
            "teams": [],  # not used by the sync
            "players": h_heat + a_heat,
        }
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


class _SeasonApi:
    """Serves the fixture season; every response is fetched at FIXTURE_FETCHED_AT."""

    def __init__(self, responses: SeasonResponses) -> None:
        self.responses = responses

    def get(self, path: str, params: Mapping[str, str] | None = None) -> ApiResponse:
        return ApiResponse(
            path=path, params=dict(params or {}), status=200, data=self.responses[path],
            error_code=None, error_message=None, fetched_at=FIXTURE_FETCHED_AT,
        )  # fmt: skip


FIXTURE_FETCHED_AT = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def synced_season(db_engine: Engine, tmp_path: Path, season_responses: SeasonResponses) -> Engine:
    """db_engine with the whole three-match fixture season synced (profiles included)."""
    with RawStore(tmp_path / "season_raw.db") as store:
        fetcher = CachedFetcher(store, _SeasonApi(season_responses), now=lambda: FIXTURE_FETCHED_AT)
        sync_season(fetcher, db_engine, "l_FIXT01", "2025/2026")
    return db_engine
