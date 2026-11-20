"""Unit tests for the PitchAPI payload parsers (hand-made fixtures; no network)."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from scouting.adapters.pitchapi.schemas import LeaguesPayload, MatchListPayload


def test_leagues_fixture_parses(load_fixture: Any) -> None:
    payload = LeaguesPayload.model_validate(load_fixture("leagues.json"))
    first, second = payload.leagues
    assert first.id == "l_FIXT01"
    assert first.country_code == "ENG"
    assert second.image_url is None  # nullable field stays None


@pytest.mark.parametrize(
    ("seasons", "latest"),
    [
        (["2024/2025", "2025/2026", "2026/2027"], "2026/2027"),
        (["2026/2027", "2024/2025"], "2026/2027"),  # doesn't depend on list order
        (["2026", "2025"], "2026"),
        (["2024", "2025", "2026/2027"], "2026/2027"),  # league that switched format
    ],
)
def test_latest_season(seasons: list[str], latest: str, load_fixture: Any) -> None:
    raw = load_fixture("leagues.json")["leagues"][0] | {"seasons": seasons}
    assert LeaguesPayload.model_validate({"leagues": [raw]}).leagues[0].latest_season == latest


def test_match_list_fixture_parses(load_fixture: Any) -> None:
    payload = MatchListPayload.model_validate(load_fixture("match_list.json"))
    assert payload.league.season == "2025/2026"
    first = payload.matches[0]
    assert first.match_date == date(2025, 8, 15)  # JSON key "date"
    assert first.time_utc == datetime(2025, 8, 15, 19, 0, tzinfo=UTC)
    assert first.home_team.name == "Example Athletic"
    assert payload.matches[1].away_team.image_url is None


def test_unknown_fields_are_ignored(load_fixture: Any) -> None:
    data = load_fixture("match_list.json")
    data["matches"][0]["venue"] = "a field PitchAPI might add later"
    MatchListPayload.model_validate(data)


def _broken(data: dict[str, Any], path: tuple[str | int, ...], value: object) -> dict[str, Any]:
    out = copy.deepcopy(data)
    node: Any = out
    for key in path[:-1]:
        node = node[key]
    if value is _DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return out


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("matches", 0, "id"), "t_FIXTA1"),  # team ID where a match ID belongs
        (("matches", 0, "id"), "m_"),  # prefix only
        (("matches", 0, "home_team", "id"), "m_FIXT01"),
        (("matches", 0, "time_utc"), "2025-08-15T19:00:00"),  # no timezone
        (("matches", 0, "date"), "15/08/2025"),
        (("matches", 0, "score_home"), "two"),
        (("matches", 0, "status"), ""),
        (("matches", 0, "home_team"), _DELETE),
        (("league", "season"), _DELETE),
        (("league", "id"), "x_FIXT01"),
    ],
    ids=[
        "wrong-id-kind",
        "prefix-only-id",
        "team-id-wrong-kind",
        "naive-kickoff",
        "bad-date",
        "non-int-score",
        "empty-status",
        "missing-home-team",
        "missing-season",
        "bad-league-id",
    ],
)
def test_match_list_bad_input_raises(
    load_fixture: Any, path: tuple[str | int, ...], value: object
) -> None:
    with pytest.raises(ValidationError):
        MatchListPayload.model_validate(_broken(load_fixture("match_list.json"), path, value))


def test_null_scores_are_allowed(load_fixture: Any) -> None:
    data = load_fixture("match_list.json")
    data["matches"][0]["score_home"] = None
    data["matches"][0]["score_away"] = None
    assert MatchListPayload.model_validate(data).matches[0].score_home is None


def test_league_without_seasons_raises(load_fixture: Any) -> None:
    raw = load_fixture("leagues.json")["leagues"][0] | {"seasons": []}
    with pytest.raises(ValidationError):
        LeaguesPayload.model_validate({"leagues": [raw]})
