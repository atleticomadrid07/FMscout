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
from scouting.db.models import Appearance, Metric, Player, PlayerSourceId, StatValue
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


# --- slice 1d registry invariants on real data --------------------------------------------


def _values(engine: Engine, code: str) -> dict[int, tuple[float | None, float | None]]:
    """appearance_id → (value, denominator) for one metric."""
    with Session(engine) as session:
        rows = session.execute(
            select(StatValue.appearance_id, StatValue.value, StatValue.denominator)
            .join(Metric, Metric.id == StatValue.metric_id)
            .where(Metric.code == code)
        ).all()
    return {appearance_id: (value, denominator) for appearance_id, value, denominator in rows}


def test_sca_breakdown_sums_to_sca(synced: Engine) -> None:
    sca = _values(synced, "sca")
    parts = [
        _values(synced, f"sca_{part}")
        for part in ("pass_live", "pass_dead", "take_on", "shot", "defensive")
    ]
    assert sca
    for appearance_id, (total, _) in sca.items():
        assert sum(part[appearance_id][0] or 0.0 for part in parts) == total


def test_keeper_rates_are_weighted_and_bounded(synced: Engine) -> None:
    alisson = _appearance(synced, "p_0DQRGp").id
    value, weight = _values(synced, "distribution_accuracy")[alisson]
    assert weight is not None and weight > 0
    assert value is not None and 0 <= value / weight <= 100  # sent as a percentage
    assert alisson not in _values(synced, "sca")  # outfield metrics skip keepers


def test_outfield_players_have_no_keeper_rows(synced: Engine) -> None:
    salah = _appearance(synced, "p_1reWwN").id
    assert salah not in _values(synced, "distribution_accuracy")
    assert salah in _values(synced, "chances_created")
