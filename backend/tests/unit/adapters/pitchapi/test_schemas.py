"""Unit tests for the PitchAPI payload parsers (hand-made fixtures; no network)."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from scouting.adapters.pitchapi.schemas import HeatmapsPayload, LeaguesPayload, MatchListPayload


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


# --- heatmaps (slice 2d) --------------------------------------------------------------------


def test_heatmaps_fixture_parses(load_fixture: Any) -> None:
    payload = HeatmapsPayload.model_validate(load_fixture("match_heatmaps.json"))
    assert payload.match_id == "m_FIXT01"
    assert (payload.grid.length, payload.grid.width, payload.grid.frame) == (16, 12, "acting_ltr")
    assert len(payload.players) == 7  # everyone who played; the unused sub has no grid
    lb = next(p for p in payload.players if p.player.id == "p_FXLB01")
    assert (lb.team.id, lb.actions, lb.cells) == ("t_FIXTA1", 8, [(4, 10, 6), (6, 10, 2)])


def _heatmaps_with(load_fixture: Any, change: Any) -> Any:
    data = copy.deepcopy(load_fixture("match_heatmaps.json"))
    change(data)
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        # depth assumes every team attacks towards high x; another frame would flip half
        (lambda d: d["grid"].update(frame="home_ltr"), "expected frame 'acting_ltr'"),
        (lambda d: d["players"][0]["cells"].append([16, 0, 1]), "outside the 16 x 12 grid"),
        (lambda d: d["players"][0]["cells"].append([0, 12, 1]), "outside the 16 x 12 grid"),
        (lambda d: d["players"][0]["cells"].append([0, 0, -1]), "greater than or equal to 0"),
        (lambda d: d["players"].append(copy.deepcopy(d["players"][0])), "has two heatmaps"),
        (lambda d: d["players"][0]["player"].update(id="t_WRONG1"), "starting with 'p_'"),
        (lambda d: d["grid"].pop("length"), "length"),
    ],
)
def test_heatmaps_bad_input_raises(load_fixture: Any, change: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        HeatmapsPayload.model_validate(_heatmaps_with(load_fixture, change))
