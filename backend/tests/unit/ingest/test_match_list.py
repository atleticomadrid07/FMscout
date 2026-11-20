"""Unit tests for the match-list sync: normalizing, idempotency, policies, error cases.

Hand-made fixtures, a temporary scouting.db built from the models, a temporary raw.db,
and a fake PitchAPI. No network.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.cache import CachedFetcher
from scouting.adapters.pitchapi.client import ApiResponse
from scouting.adapters.pitchapi.raw_store import RawStore
from scouting.adapters.pitchapi.schemas import League, LeaguesPayload, MatchListPayload
from scouting.db.base import Base, make_engine
from scouting.db.models import (
    Competition,
    Match,
    MatchSourceId,
    Season,
    SyncState,
    Team,
    TeamSourceId,
)
from scouting.ingest.match_list import (
    MatchListSummary,
    SyncError,
    normalize_match_list,
    sync_match_list,
)

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
LEAGUES_PATH = "/v1/leagues"
MATCHES_PATH = "/v1/leagues/l_FIXT01/matches"


class FakeApi:
    """Answers by path from a dict; records every call. `clock` stamps fetched_at."""

    def __init__(self, responses: dict[str, Any], clock: list[datetime]) -> None:
        self.responses = responses
        self.clock = clock
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, path: str, params: Mapping[str, str] | None = None) -> ApiResponse:
        query = dict(params or {})
        self.calls.append((path, query))
        data = self.responses[path]
        return ApiResponse(
            path=path,
            params=query,
            status=200,
            data=data,
            error_code=None,
            error_message=None,
            fetched_at=self.clock[0],
        )


@pytest.fixture
def raw_store(tmp_path: Path) -> Iterator[RawStore]:
    store = RawStore(tmp_path / "raw.db")
    yield store
    store.close()


@pytest.fixture
def clock() -> list[datetime]:
    return [T0]  # a list so tests can move time forward


@pytest.fixture
def api(load_fixture: Any, clock: list[datetime]) -> FakeApi:
    return FakeApi(
        {LEAGUES_PATH: load_fixture("leagues.json"), MATCHES_PATH: load_fixture("match_list.json")},
        clock,
    )


def run_sync(
    engine: Engine, raw_store: RawStore, api: FakeApi | None, season: str = "2025/2026"
) -> MatchListSummary:
    fetcher = CachedFetcher(raw_store, api, now=lambda: api.clock[0] if api else T0)
    with Session(engine) as session, session.begin():
        return sync_match_list(fetcher, session, "l_FIXT01", season)


def count(engine: Engine, model: type[Any]) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


# --- first sync ---------------------------------------------------------------------------


def test_first_sync_inserts_everything(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    summary = run_sync(db_engine, raw_store, api)

    assert (summary.teams.inserted, summary.matches.inserted) == (3, 3)
    assert summary.competition == "Fixture League"
    assert summary.is_current is False  # 2026/2027 is the latest fixture season
    assert count(db_engine, Competition) == 1
    assert count(db_engine, Season) == 1
    assert count(db_engine, Team) == 3  # each team stored once despite appearing twice
    assert count(db_engine, TeamSourceId) == 3
    assert count(db_engine, Match) == 3
    assert count(db_engine, MatchSourceId) == 3


def test_first_sync_stores_match_fields(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    run_sync(db_engine, raw_store, api)
    with Session(db_engine) as session:
        match = session.scalars(
            select(Match)
            .join(MatchSourceId, MatchSourceId.match_id == Match.id)
            .where(MatchSourceId.external_id == "m_FIXT03")
        ).one()
        home = session.get(Team, match.home_team_id)
        assert home is not None and home.name == "Placeholder Town"
        assert home.image_url is None
        assert match.kickoff_utc == datetime(2025, 8, 23, 23, 30)  # stored as naive UTC
        assert match.match_date.isoformat() == "2025-08-23"
        assert (match.home_score, match.away_score) == (1, 3)
        assert match.status == "finished"
        # per-match statuses wait for slice 1c
        assert (match.players_status, match.advanced_status, match.lineups_status) == (
            "pending",
            "pending",
            "pending",
        )


def test_sync_state_records_match_list_fetch_time(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    run_sync(db_engine, raw_store, api)
    with Session(db_engine) as session:
        (state,) = session.scalars(select(SyncState)).all()
        assert state.match_list_fetched_at == T0.replace(tzinfo=None)


# --- idempotency and caching --------------------------------------------------------------------


def test_second_sync_changes_nothing_and_uses_cache(
    db_engine: Engine, raw_store: RawStore, api: FakeApi, snapshot: Any, clock: list[datetime]
) -> None:
    run_sync(db_engine, raw_store, api)
    before = snapshot(db_engine)

    clock[0] = T0 + timedelta(days=1)
    summary = run_sync(db_engine, raw_store, api)

    assert snapshot(db_engine) == before  # identical database
    assert (summary.teams.inserted, summary.teams.updated) == (0, 0)
    assert (summary.matches.inserted, summary.matches.updated) == (0, 0)
    assert summary.matches.unchanged == 3
    assert summary.match_list_from_cache  # completed season: never refetched
    assert len(api.calls) == 2  # leagues + match list, both on the first run only


def test_offline_sync_rebuilds_identical_database(
    db_engine: Engine, raw_store: RawStore, api: FakeApi, snapshot: Any, tmp_path: Path
) -> None:
    run_sync(db_engine, raw_store, api)
    other = make_engine(f"sqlite:///{tmp_path / 'rebuilt.db'}")
    Base.metadata.create_all(other)
    run_sync(other, raw_store, None)  # offline: raw.db only
    assert snapshot(other) == snapshot(db_engine)
    other.dispose()


def test_current_season_match_list_is_refetched(
    db_engine: Engine, raw_store: RawStore, api: FakeApi, load_fixture: Any
) -> None:
    current = load_fixture("match_list.json")
    current["league"]["season"] = "2026/2027"
    api.responses[MATCHES_PATH] = current

    first = run_sync(db_engine, raw_store, api, season="2026/2027")
    second = run_sync(db_engine, raw_store, api, season="2026/2027")

    assert first.is_current and not second.match_list_from_cache
    match_list_calls = [c for c in api.calls if c[0] == MATCHES_PATH]
    assert len(match_list_calls) == 2


def test_changed_score_is_updated_not_duplicated(
    db_engine: Engine, raw_store: RawStore, load_fixture: Any
) -> None:
    league = LeaguesPayload.model_validate(load_fixture("leagues.json")).leagues[0]
    data = load_fixture("match_list.json")
    with Session(db_engine) as session, session.begin():
        normalize_match_list(session, league, MatchListPayload.model_validate(data), fetched_at=T0)

    corrected = copy.deepcopy(data)
    corrected["matches"][0]["score_home"] = 3
    with Session(db_engine) as session, session.begin():
        summary = normalize_match_list(
            session, league, MatchListPayload.model_validate(corrected), fetched_at=T0
        )

    assert (summary.matches.updated, summary.matches.unchanged) == (1, 2)
    assert count(db_engine, Match) == 3


# --- seasons and teams across syncs -------------------------------------------------------------


def _league(load_fixture: Any, seasons: list[str]) -> League:
    raw = load_fixture("leagues.json")["leagues"][0] | {"seasons": seasons}
    return LeaguesPayload.model_validate({"leagues": [raw]}).leagues[0]


def test_is_current_moves_when_a_new_season_appears(db_engine: Engine, load_fixture: Any) -> None:
    payload = MatchListPayload.model_validate(load_fixture("match_list.json"))
    with Session(db_engine) as session, session.begin():
        # 2025/2026 is the latest season at first...
        normalize_match_list(session, _league(load_fixture, ["2025/2026"]), payload, fetched_at=T0)
    with Session(db_engine) as session:
        assert session.scalars(select(Season.is_current)).one() is True

    later = load_fixture("match_list.json")
    later["league"]["season"] = "2026/2027"
    for row in later["matches"]:
        row["id"] = row["id"].replace("FIXT", "NEXT")
    with Session(db_engine) as session, session.begin():
        # ...until 2026/2027 is listed and synced.
        normalize_match_list(
            session,
            _league(load_fixture, ["2025/2026", "2026/2027"]),
            MatchListPayload.model_validate(later),
            fetched_at=T0,
        )
    with Session(db_engine) as session:
        flags = {
            label: current
            for label, current in session.execute(select(Season.label, Season.is_current))
        }
    assert flags == {"2025/2026": False, "2026/2027": True}


def test_older_season_does_not_rename_teams(db_engine: Engine, load_fixture: Any) -> None:
    league = _league(load_fixture, ["2024/2025", "2025/2026"])
    current = MatchListPayload.model_validate(load_fixture("match_list.json"))
    with Session(db_engine) as session, session.begin():
        normalize_match_list(session, league, current, fetched_at=T0)

    old = load_fixture("match_list.json")
    old["league"]["season"] = "2024/2025"
    for row in old["matches"]:
        row["id"] = row["id"].replace("FIXT", "OLDS")
        for side in ("home_team", "away_team"):
            if row[side]["id"] == "t_FIXTA1":
                row[side]["name"] = "Example Athletic (old name)"
    with Session(db_engine) as session, session.begin():
        summary = normalize_match_list(
            session, league, MatchListPayload.model_validate(old), fetched_at=T0
        )
        names = set(session.scalars(select(Team.name)))

    assert "Example Athletic" in names and "Example Athletic (old name)" not in names
    assert summary.teams.inserted == 0
    assert count(db_engine, Match) == 6


# --- errors -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("league_id", "season", "message"),
    [
        ("l_NOPE00", "2025/2026", "no league"),
        ("l_FIXT01", "2019/2020", "has no season"),
    ],
)
def test_bad_league_or_season_raises(
    db_engine: Engine, raw_store: RawStore, api: FakeApi, league_id: str, season: str, message: str
) -> None:
    fetcher = CachedFetcher(raw_store, api, now=lambda: T0)
    with pytest.raises(SyncError, match=message), Session(db_engine) as session:
        sync_match_list(fetcher, session, league_id, season)


def test_season_mismatch_in_payload_raises(
    db_engine: Engine, raw_store: RawStore, api: FakeApi, load_fixture: Any
) -> None:
    wrong = load_fixture("match_list.json")
    wrong["league"]["season"] = "2024/2025"
    api.responses[MATCHES_PATH] = wrong
    with pytest.raises(SyncError, match="asked for season"):
        run_sync(db_engine, raw_store, api)


def test_malformed_payload_raises_sync_error_and_writes_nothing(
    db_engine: Engine, raw_store: RawStore, api: FakeApi, load_fixture: Any
) -> None:
    bad = load_fixture("match_list.json")
    del bad["matches"][1]["away_team"]
    api.responses[MATCHES_PATH] = bad
    with pytest.raises(SyncError, match="unexpected response shape"):
        run_sync(db_engine, raw_store, api)
    assert count(db_engine, Competition) == 0


def test_duplicate_match_ids_raise(db_engine: Engine, load_fixture: Any) -> None:
    data = load_fixture("match_list.json")
    data["matches"].append(copy.deepcopy(data["matches"][0]))
    league = LeaguesPayload.model_validate(load_fixture("leagues.json")).leagues[0]
    with pytest.raises(SyncError, match="repeats match IDs"), Session(db_engine) as session:
        normalize_match_list(session, league, MatchListPayload.model_validate(data), fetched_at=T0)


def test_same_team_both_sides_raises(db_engine: Engine, load_fixture: Any) -> None:
    data = load_fixture("match_list.json")
    data["matches"][0]["away_team"] = copy.deepcopy(data["matches"][0]["home_team"])
    league = LeaguesPayload.model_validate(load_fixture("leagues.json")).leagues[0]
    with pytest.raises(SyncError, match="same team"), Session(db_engine) as session:
        normalize_match_list(session, league, MatchListPayload.model_validate(data), fetched_at=T0)


def test_error_mid_sync_rolls_back_everything(
    db_engine: Engine, raw_store: RawStore, api: FakeApi, load_fixture: Any
) -> None:
    data = load_fixture("match_list.json")
    data["matches"][2]["away_team"] = copy.deepcopy(data["matches"][2]["home_team"])
    api.responses[MATCHES_PATH] = data
    with pytest.raises(SyncError):
        run_sync(db_engine, raw_store, api)
    assert count(db_engine, Team) == 0
    assert count(db_engine, Match) == 0
