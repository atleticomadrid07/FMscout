"""Integration test on the real EPL 2025/26 match list saved by the Phase 0 spike.

The spike's files live in backend/spikes/data/, which is gitignored (no PitchAPI data
in the repo until the data license is confirmed, D-007). Without them this test skips.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.schemas import LeaguesPayload, MatchListPayload
from scouting.db.models import Match, Season, Team
from scouting.ingest.match_list import normalize_match_list

SPIKE_DATA = Path(__file__).resolve().parents[2] / "spikes" / "data"
LEAGUES_FILE = SPIKE_DATA / "leagues.json"
EPL_FILE = SPIKE_DATA / "matches_l-4WFCIZ_2025-2026_played.json"
FETCHED_AT = datetime(2026, 11, 19, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    not (LEAGUES_FILE.exists() and EPL_FILE.exists()),
    reason="spike data not present in backend/spikes/data/",
)


def _spike_data(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))["data"]


def _sync(engine: Engine) -> None:
    leagues = LeaguesPayload.model_validate(_spike_data(LEAGUES_FILE))
    epl = next(lg for lg in leagues.leagues if lg.id == "l_4WFCIZ")
    payload = MatchListPayload.model_validate(_spike_data(EPL_FILE))
    with Session(engine) as session, session.begin():
        normalize_match_list(session, epl, payload, fetched_at=FETCHED_AT)


def test_epl_2025_26_loads_380_matches_and_20_teams(db_engine: Engine) -> None:
    _sync(db_engine)
    with Session(db_engine) as session:
        assert session.scalar(select(func.count()).select_from(Match)) == 380
        assert session.scalar(select(func.count()).select_from(Team)) == 20
        season = session.scalars(select(Season)).one()
        assert (season.label, season.is_current) == ("2025/2026", False)
        # every team plays 19 home and 19 away matches
        home_counts = session.execute(
            select(Match.home_team_id, func.count()).group_by(Match.home_team_id)
        ).all()
        assert {n for _, n in home_counts} == {19}


def test_epl_sync_twice_gives_identical_database(db_engine: Engine, snapshot: Any) -> None:
    _sync(db_engine)
    before = snapshot(db_engine)
    _sync(db_engine)
    assert snapshot(db_engine) == before
