"""Integration test on the real Liverpool v Bournemouth (m_1t3Zja) files saved by the
Phase 0 spike. Skips when backend/spikes/data/ doesn't have them (it's gitignored, D-007).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.raw_store import RawRecord
from scouting.adapters.pitchapi.schemas import LeaguesPayload, MatchListPayload
from scouting.db.models import Appearance, Player, PlayerSourceId
from scouting.ingest.match_detail import find_match, normalize_match
from scouting.ingest.match_list import normalize_match_list

SPIKE_DATA = Path(__file__).resolve().parents[2] / "spikes" / "data"
FILES = {
    "leagues": SPIKE_DATA / "leagues.json",
    "match_list": SPIKE_DATA / "matches_l-4WFCIZ_2025-2026_played.json",
    "players": SPIKE_DATA / "match_m-1t3Zja_players.json",
    "advanced": SPIKE_DATA / "match_m-1t3Zja_advanced-players.json",
    "lineups": SPIKE_DATA / "match_m-1t3Zja_lineups.json",
}

pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in FILES.values()),
    reason="spike data for m_1t3Zja not present in backend/spikes/data/",
)


def _record(path: Path) -> RawRecord:
    """Spike files hold {path, params, status, fetched_at, data, error}."""
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    error = raw.get("error") or {}
    return RawRecord(
        cache_key=f"GET {raw['path']}",
        path=raw["path"],
        params=raw.get("params") or {},
        status=raw["status"],
        error_code=error.get("code"),
        error_message=error.get("message"),
        data=raw["data"],
        fetched_at=datetime.fromisoformat(raw["fetched_at"]),
    )


@pytest.fixture
def synced(db_engine: Engine) -> Engine:
    leagues = LeaguesPayload.model_validate(_record(FILES["leagues"]).data)
    epl = next(lg for lg in leagues.leagues if lg.id == "l_4WFCIZ")
    match_list = _record(FILES["match_list"])
    with Session(db_engine) as session, session.begin():
        normalize_match_list(
            session,
            epl,
            MatchListPayload.model_validate(match_list.data),
            fetched_at=match_list.fetched_at,
        )
        normalize_match(
            session,
            find_match(session, "m_1t3Zja"),
            players=_record(FILES["players"]),
            advanced=_record(FILES["advanced"]),
            lineups=_record(FILES["lineups"]),
        )
    return db_engine


def _appearance(engine: Engine, player_id: str) -> Appearance:
    with Session(engine) as session:
        return session.scalars(
            select(Appearance)
            .join(PlayerSourceId, PlayerSourceId.player_id == Appearance.player_id)
            .where(PlayerSourceId.external_id == player_id)
        ).one()


def test_every_starter_gets_a_role(synced: Engine) -> None:
    with Session(synced) as session:
        starters = session.scalars(select(Appearance).where(Appearance.is_starter.is_(True))).all()
        assert len(starters) == 22
        assert all(a.role is not None for a in starters)  # 4-2-3-1 and 4-1-4-1 are mapped
        keepers = session.scalar(
            select(func.count()).select_from(Appearance).where(Appearance.is_goalkeeper)
        )
        assert (keepers or 0) >= 2


@pytest.mark.parametrize(
    ("player_id", "name", "role", "side"),
    [
        ("p_1cvezl", "Kerkez", "FB", "L"),
        ("p_0dFlES", "Frimpong", "FB", "R"),
        ("p_0DQRGp", "Alisson", "GK", "C"),
        ("p_06lEAz", "Wirtz", "AM", "C"),
        ("p_1reWwN", "Salah", "W", "R"),
    ],
)
def test_known_players_get_the_right_role(
    synced: Engine, player_id: str, name: str, role: str, side: str
) -> None:
    appearance = _appearance(synced, player_id)
    assert (appearance.role, appearance.side) == (role, side), name


def test_canonical_minutes_cap_at_90(synced: Engine) -> None:
    with Session(synced) as session:
        assert session.scalar(select(func.max(Appearance.minutes))) == 90
        assert (session.scalar(select(func.count()).select_from(Player)) or 0) >= 22
