"""Unit tests for the season crawl (slice 1d): processing, --limit, resume after a crash,
Ctrl-C, failures that don't block the season, renormalize, profiles, and the reports.

Uses the three-match fixture season from conftest.py (hand-made data, not PitchAPI's).
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.cache import CachedFetcher
from scouting.adapters.pitchapi.client import ApiResponse
from scouting.adapters.pitchapi.raw_store import RawStore
from scouting.db.base import Base, make_engine
from scouting.db.models import Match, Player, PlayerSourceId, SyncState
from scouting.db.reports import checksum, formation_report
from scouting.ingest.profiles import sync_profiles
from scouting.ingest.progress import Progress
from scouting.ingest.season import SeasonSummary, find_season, sync_season

T0 = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
LEAGUE, SEASON = "l_FIXT01", "2025/2026"
NO_PROFILE = "p_CST01"  # this player's profile returns 404


class CrashError(Exception):
    """Stands in for a power cut or a killed process."""


class FakeApi:
    def __init__(self, responses: Mapping[str, Any]) -> None:
        self.responses = dict(responses)
        self.calls: list[str] = []
        self.fail_at: int | None = None  # 1-based call number
        self.fail_with: BaseException = CrashError("simulated crash")
        self.not_found: dict[str, str] = {f"/v1/players/{NO_PROFILE}": "RESOURCE_NOT_FOUND"}

    def get(self, path: str, params: Mapping[str, str] | None = None) -> ApiResponse:
        self.calls.append(path)
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise self.fail_with
        code = self.not_found.get(path)
        return ApiResponse(
            path=path,
            params=dict(params or {}),
            status=404 if code else 200,
            data=None if code else self.responses[path],
            error_code=code,
            error_message="msg" if code else None,
            fetched_at=T0,
        )


@pytest.fixture
def api(season_responses: dict[str, Any]) -> FakeApi:
    return FakeApi(season_responses)


@pytest.fixture
def raw_store(tmp_path: Path) -> Iterator[RawStore]:
    with RawStore(tmp_path / "raw.db") as store:
        yield store


def fresh_db(tmp_path: Path, name: str) -> Engine:
    engine = make_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return engine


def run(engine: Engine, store: RawStore, api: FakeApi | None, **kwargs: Any) -> SeasonSummary:
    return sync_season(CachedFetcher(store, api, now=lambda: T0), engine, LEAGUE, SEASON, **kwargs)


def statuses(engine: Engine) -> list[tuple[str, str, str]]:
    with Session(engine) as session:
        return [
            (m.players_status, m.advanced_status, m.lineups_status)
            for m in session.scalars(select(Match).order_by(Match.kickoff_utc))
        ]


# --- a full run ---------------------------------------------------------------------------


def test_full_run_processes_every_match_and_profiles(db_engine: Engine, raw_store: RawStore,
                                                      api: FakeApi) -> None:  # fmt: skip
    summary = run(db_engine, raw_store, api)
    assert (summary.to_process, summary.processed) == (3, 3)
    assert statuses(db_engine) == [("ok", "ok", "ok")] * 3
    assert summary.failed == [] and not summary.interrupted and summary.still_pending == 0
    assert summary.profiles is not None
    assert (summary.profiles.updated, summary.profiles.not_found) == (13, 1)  # 14 players
    assert len(api.calls) == 2 + 3 * 3 + 14


def test_profiles_fill_player_fields(db_engine: Engine, raw_store: RawStore, api: FakeApi) -> None:
    run(db_engine, raw_store, api)
    with Session(db_engine) as session:
        player = session.scalars(
            select(Player)
            .join(PlayerSourceId, PlayerSourceId.player_id == Player.id)
            .where(PlayerSourceId.external_id == "p_ALB01")
        ).one()
        assert (player.short_name, player.shirt_number, player.country_code) == (
            "B. Fullbäck",
            "7",
            "ENG",
        )
        assert player.listed_slot_code == 2
        assert player.profile_fetched_at == T0.replace(tzinfo=None)


def test_second_run_makes_no_requests_and_changes_nothing(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    run(db_engine, raw_store, api)
    before = checksum(db_engine)
    calls = len(api.calls)
    summary = run(db_engine, raw_store, api)
    assert summary.processed == 0  # nothing left to do
    assert len(api.calls) == calls  # completed season, finished matches: all from cache
    assert checksum(db_engine) == before


def test_last_full_sync_is_recorded_deterministically(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    run(db_engine, raw_store, api)
    with Session(db_engine) as session:
        (state,) = session.scalars(select(SyncState)).all()
        assert state.last_full_sync_at == T0.replace(tzinfo=None)


# --- chunks, crashes, Ctrl-C ----------------------------------------------------------------


def test_limit_processes_in_chunks(db_engine: Engine, raw_store: RawStore, api: FakeApi) -> None:
    first = run(db_engine, raw_store, api, limit=1)
    assert (first.processed, first.still_pending) == (1, 2)
    second = run(db_engine, raw_store, api, limit=5)
    assert (second.to_process, second.processed, second.still_pending) == (2, 2, 0)


def test_resume_after_crash_equals_an_uninterrupted_run(
    tmp_path: Path, raw_store: RawStore, api: FakeApi, season_responses: dict[str, Any]
) -> None:
    # Reference: one uninterrupted run into its own databases.
    reference = fresh_db(tmp_path, "reference.db")
    with RawStore(tmp_path / "reference_raw.db") as reference_raw:
        run(reference, reference_raw, FakeApi(season_responses))

    # CrashError on call 6: leagues, match list, m_FIXT01 x3 succeed; m_FIXT02's first call dies.
    crashed = fresh_db(tmp_path, "crashed.db")
    api.fail_at = 6
    with pytest.raises(CrashError):
        run(crashed, raw_store, api)
    assert statuses(crashed) == [("ok", "ok", "ok"), ("pending",) * 3, ("pending",) * 3]

    api.fail_at = None
    resumed = run(crashed, raw_store, api)
    assert resumed.processed == 2  # only the matches still pending
    assert checksum(crashed) == checksum(reference)


def test_ctrl_c_stops_cleanly_and_skips_profiles(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    api.fail_at, api.fail_with = 7, KeyboardInterrupt()  # during m_FIXT02
    summary = run(db_engine, raw_store, api)
    assert summary.interrupted and summary.profiles is None
    assert summary.processed == 1
    assert statuses(db_engine)[1] == ("pending", "pending", "pending")  # rolled back


# --- failures and re-checks -------------------------------------------------------------------


def test_bad_match_is_reported_and_does_not_block_the_season(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    broken = copy.deepcopy(api.responses["/v1/matches/m_FIXT02/players"])
    broken[0]["team_id"] = "t_NOPE00"  # a team that isn't in the match
    api.responses["/v1/matches/m_FIXT02/players"] = broken
    summary = run(db_engine, raw_store, api)
    assert [match_id for match_id, _ in summary.failed] == ["m_FIXT02"]
    assert summary.processed == 2
    assert statuses(db_engine)[1] == ("pending", "pending", "pending")  # retried next run


def test_unavailable_analytics_are_rechecked_from_cache(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    api.not_found["/v1/matches/m_FIXT03/advanced/players"] = "ANALYTICS_UNAVAILABLE"
    first = run(db_engine, raw_store, api)
    assert first.advanced_unavailable == 1
    calls = len(api.calls)
    second = run(db_engine, raw_store, api)
    assert second.processed == 1  # m_FIXT03 is looked at again...
    assert len(api.calls) == calls  # ...but the 404 is still fresh: no request


def test_offline_renormalize_rebuilds_an_identical_database(
    db_engine: Engine, raw_store: RawStore, api: FakeApi, tmp_path: Path
) -> None:
    run(db_engine, raw_store, api)
    rebuilt = fresh_db(tmp_path, "rebuilt.db")
    summary = run(rebuilt, raw_store, None, renormalize=True)
    assert summary.processed == 3
    assert summary.profiles is not None and summary.profiles.not_cached == 0
    assert checksum(rebuilt) == checksum(db_engine)


def test_renormalize_reprocesses_matches_already_ok(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    run(db_engine, raw_store, api)
    summary = run(db_engine, raw_store, None, renormalize=True)
    assert summary.processed == 3


def test_offline_without_cached_profiles_counts_them(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    run(db_engine, raw_store, api, profiles=False)
    summary = run(db_engine, raw_store, None, renormalize=True)
    assert summary.profiles is not None and summary.profiles.not_cached == 14


# --- reports ------------------------------------------------------------------------------------


def test_checksum_counts_rows(db_engine: Engine, raw_store: RawStore, api: FakeApi) -> None:
    run(db_engine, raw_store, api)
    digest, counts = checksum(db_engine)
    assert len(digest) == 64
    assert (counts["match"], counts["appearance"], counts["player"]) == (3, 21, 14)


def test_formation_report(db_engine: Engine, raw_store: RawStore, api: FakeApi) -> None:
    api.responses["/v1/matches/m_FIXT02/lineups"]["away"]["formation"] = "4-2-4"
    run(db_engine, raw_store, api)
    with Session(db_engine) as session:
        report = formation_report(session, find_season(session, LEAGUE, SEASON).id)
    assert report.matches == 3
    assert report.formations == {"4-2-3-1": 2, "3-4-2-1": 3, "4-2-4": 1}
    assert report.unmapped_formations == ["4-2-4"]
    assert report.unmapped_slots == {
        ("4-2-4", 11): 1,
        ("4-2-4", 38): 1,
        ("4-2-4", 115): 1,
    }


# --- profiles: chunks, Ctrl-C, bad payloads (fix after the EPL crawl) ---------------------


def profile_fields(engine: Engine) -> list[tuple[str | None, datetime | None]]:
    with Session(engine) as session:
        return [
            (p.short_name, p.profile_fetched_at)
            for p in session.scalars(select(Player).order_by(Player.id))
        ]


def run_profiles(engine: Engine, store: RawStore, api: FakeApi, **kwargs: Any) -> Any:
    return sync_profiles(CachedFetcher(store, api, now=lambda: T0), engine, **kwargs)


def test_ctrl_c_during_profiles_keeps_finished_chunks(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    run(db_engine, raw_store, api, profiles=False)
    calls = len(api.calls)
    api.fail_at, api.fail_with = calls + 8, KeyboardInterrupt()  # in the second chunk of 5
    summary = run_profiles(db_engine, raw_store, api, chunk_size=5)
    assert summary.interrupted
    assert summary.updated == 5  # the first chunk was saved; the second rolled back
    assert sum(1 for _, fetched in profile_fields(db_engine) if fetched) == 5

    api.fail_at = None
    resumed = run_profiles(db_engine, raw_store, api, chunk_size=5)
    assert (resumed.updated, resumed.unchanged, resumed.not_found) == (8, 5, 1)
    # 8 calls before the stop (the 8th failed), then only the 7 never fetched
    assert len(api.calls) == calls + 8 + 7


def test_ctrl_c_during_season_profiles_marks_the_run_interrupted(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    api.fail_at, api.fail_with = 2 + 9 + 3, KeyboardInterrupt()  # third profile request
    summary = run(db_engine, raw_store, api)
    assert summary.processed == 3 and summary.interrupted
    assert summary.profiles is not None and summary.profiles.interrupted
    with Session(db_engine) as session:
        assert session.scalars(select(SyncState)).one().last_full_sync_at is None


def test_bad_profile_is_reported_and_skipped(
    db_engine: Engine, raw_store: RawStore, api: FakeApi
) -> None:
    api.responses["/v1/players/p_ALB01"] = {"id": "p_ALB01", "name": ""}  # empty name
    summary = run(db_engine, raw_store, api)
    assert summary.profiles is not None
    assert [player for player, _ in summary.profiles.failed] == ["p_ALB01"]
    assert (summary.profiles.updated, summary.profiles.not_found) == (12, 1)
    assert not summary.interrupted


def test_profile_progress_is_reported(db_engine: Engine, raw_store: RawStore, api: FakeApi) -> None:
    run(db_engine, raw_store, api, profiles=False)
    seen: list[Progress] = []
    run_profiles(db_engine, raw_store, api, chunk_size=5, on_progress=seen.append)
    assert [(p.done, p.total, p.unit) for p in seen] == [
        (5, 14, "players"),
        (10, 14, "players"),
        (14, 14, "players"),
    ]
