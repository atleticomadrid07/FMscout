"""Slice 3c: a player through a template (radar data) and a peer group on chosen metrics,
over the hand-made three-match fixture season (not real PitchAPI data). Fixture groups:
GK 4, FB 2, W 2, ST 2 at 45 min; no CB/DM/CM/AM."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from scouting.api.app import create_app
from scouting.api.mapping import MAX_METRICS
from scouting.api.routes.groups import sort_members
from scouting.cli.main import app as cli_app
from scouting.db.queries import GroupMember, GroupStat
from scouting.metrics.registry import BY_CODE
from scouting.metrics.templates import builtin_templates

KEY = "l_FIXT01.2025-2026"
SEASON_ARGS = ("--league", "l_FIXT01", "--season", "2025/2026")
runner = CliRunner()


def cli(*args: str) -> Any:
    return runner.invoke(cli_app, list(args))


@pytest.fixture
def client(api_db: str) -> Iterator[TestClient]:
    result = cli("sync", "season", *SEASON_ARGS, "--offline")
    assert result.exit_code == 0, result.output
    with TestClient(create_app(api_db)) as test_client:
        yield test_client


def get(client: TestClient, path: str, **params: Any) -> Any:
    response = client.get(f"/api/v1{path}", params={"scope": KEY, **params})
    assert response.status_code == 200, response.text
    return response.json()


def error(client: TestClient, path: str, **params: Any) -> tuple[int, str]:
    response = client.get(f"/api/v1{path}", params={"scope": KEY, **params})
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    return response.status_code, body["error"]["code"]


def rows(body: Any) -> list[Any]:
    return [row for section in body["sections"] for row in section["rows"]]


# --- /players/{id}/template --------------------------------------------------------------------


def test_template_defaults_to_his_own_role(client: TestClient) -> None:
    body = get(client, "/players/p_CST01/template")
    assert body["template"] == {
        "id": "builtin:ST", "label": "Striker", "peer_role": "ST", "same_group": True,
    }  # fmt: skip
    assert body["player"] == {"id": "p_CST01", "name": "Cal Striker"} and body["minutes"] == 156
    assert body["group"]["role"] == "ST" and body["group"]["size"] == 2
    template = builtin_templates()["ST"]
    assert [s["name"] for s in body["sections"]] == [s.name for s in template.sections]
    assert [r["metric"] for r in rows(body)] == template.codes


def test_template_rows_are_the_stat_lines(client: TestClient) -> None:
    template = get(client, "/players/p_CST01/template")
    stats = get(
        client, "/players/p_CST01/stats", metrics=",".join(r["metric"] for r in rows(template))
    )
    assert rows(template) == stats["stats"]  # the same fields and numbers as /stats
    passes = next(r for r in rows(template) if r["metric"] == "passes_completed")
    assert passes["pct"] is None and passes["reason"] == "needs 100 attempts (has 36)"


def test_template_by_id_or_role_and_for_another_position(client: TestClient) -> None:
    by_id = get(client, "/players/p_CST01/template", template="builtin:W")
    assert get(client, "/players/p_CST01/template", template="W") == by_id
    assert by_id["template"]["same_group"] is False and by_id["template"]["peer_role"] == "W"
    assert by_id["group"]["role"] == "ST"  # still ranked against his own group
    assert [r["metric"] for r in rows(by_id)] == builtin_templates()["W"].codes


def test_template_rows_without_data(client: TestClient) -> None:
    body = get(client, "/players/p_CST01/template", template="GK")  # keeper metrics
    saves = next(r for r in rows(body) if r["metric"] == "saves")
    assert (saves["minutes"], saves["matches"], saves["per90"], saves["pct"]) == (0, 0, None, None)
    assert saves["reason"] == "no data" and saves["basis"] == "per90"


def test_template_for_an_unranked_player(client: TestClient) -> None:
    body = get(client, "/players/p_AGK03/template")  # a keeper with 5 minutes
    assert body["group"] is None and body["template"]["id"] == "builtin:GK"
    assert {r["reason"] for r in rows(body)} == {"not ranked: below 45 min"}


def test_template_errors(client: TestClient) -> None:
    assert error(client, "/players/p_ASB01/template") == (400, "TEMPLATE_REQUIRED")  # no role
    assert get(client, "/players/p_ASB01/template", template="ST")["group"] is None
    assert error(client, "/players/p_CST01/template", template="nope") == (404, "UNKNOWN_TEMPLATE")
    assert error(client, "/players/p_NOPE/template") == (404, "PLAYER_NOT_FOUND")


# --- /groups/{role} ----------------------------------------------------------------------------


def test_group_defaults_to_the_role_template(client: TestClient) -> None:
    body = get(client, "/groups/ST")
    codes = builtin_templates()["ST"].codes
    assert [m["metric"] for m in body["metrics"]] == codes and body["sort"] == codes[0]
    assert body["group"] == {
        "role": "ST", "size": 2, "min_minutes": 45, "small_sample": True, "stored": True,
    }  # fmt: skip
    assert body["provenance"]["scope"] == KEY
    assert {p["id"] for p in body["players"]} == {"p_AST01", "p_CST01"}
    assert all(set(p["values"]) == set(codes) for p in body["players"])


def test_group_values_match_each_players_stats(client: TestClient) -> None:
    codes = "saves,goals_conceded,distribution_accuracy"
    body = get(client, "/groups/GK", metrics=codes)
    assert len(body["players"]) == body["group"]["size"] == 4  # the 5-minute keeper isn't in
    for player in body["players"]:
        lines = {
            s["metric"]: s
            for s in get(client, f"/players/{player['id']}/stats", metrics=codes)["stats"]
        }
        for code, value in player["values"].items():
            line = lines[code]
            expected = line["rate"] if line["basis"] == "rate" else line["per90"]
            assert value["value"] == pytest.approx(expected)
            assert (value["pct"], value["reason"], value["minutes"]) == (
                line["pct"], line["reason"], line["minutes"],
            )  # fmt: skip
            assert value["attempts"] == line["attempts"] and value["total"] == line["total"]


def test_group_metric_header(client: TestClient) -> None:
    metrics = {
        m["metric"]: m
        for m in get(client, "/groups/GK", metrics="saves,distribution_accuracy,npxg")["metrics"]
    }
    assert metrics["saves"] == {
        "metric": "saves", "label": BY_CODE["saves"].label, "basis": "per90", "rate_scale": None,
        "model": False, "direction": "higher", "ranked_among": 4,
    }  # fmt: skip
    accuracy = metrics["distribution_accuracy"]
    assert (accuracy["basis"], accuracy["rate_scale"], accuracy["ranked_among"]) == (
        "rate",
        "percent",
        0,
    )
    assert metrics["npxg"]["model"] is True and metrics["npxg"]["ranked_among"] == 0  # outfield


def test_group_sorting(client: TestClient) -> None:
    saves = get(client, "/groups/GK", metrics="saves,goals_conceded")
    values = [p["values"]["saves"]["value"] for p in saves["players"]]
    assert values == sorted(values, reverse=True)  # best first
    conceded = get(client, "/groups/GK", metrics="saves,goals_conceded", sort="goals_conceded")
    assert conceded["sort"] == "goals_conceded"
    values = [p["values"]["goals_conceded"]["value"] for p in conceded["players"]]
    assert values == sorted(values)  # lower is better: fewest first


def test_metrics_with_no_data_say_so(client: TestClient) -> None:
    body = get(client, "/groups/GK", metrics="npxg")
    assert all(
        p["values"]["npxg"] == {
            "value": None, "total": None, "attempts": None, "minutes": 0, "pct": None,
            "reason": "no data",
        }
        for p in body["players"]
    )  # fmt: skip


def test_sort_members_puts_missing_values_last() -> None:
    def member(name: str, per90: float | None) -> GroupMember:
        stats = (
            {} if per90 is None else {"shots": GroupStat(per90, None, 900, per90, None, None, None)}
        )
        return GroupMember(0, f"p_{name}", name, "T", 900, stats)

    people = [member("D", None), member("B", 1.0), member("A", 2.0), member("C", 1.0)]
    assert [m.name for m in sort_members(people, BY_CODE["shots"])] == ["A", "B", "C", "D"]
    lower = BY_CODE["goals_conceded"]
    conceded = [
        GroupMember(
            0, "p_x", n, "T", 900, {"goals_conceded": GroupStat(v, None, 900, v, None, None, None)}
        )
        for n, v in (("X", 2.0), ("Y", 0.5))
    ]
    assert [m.name for m in sort_members(conceded, lower)] == ["Y", "X"]


def test_group_errors(client: TestClient) -> None:
    assert error(client, "/groups/XX") == (400, "UNKNOWN_ROLE")
    assert error(client, "/groups/CB") == (404, "GROUP_NOT_FOUND")  # no CB in the fixture season
    assert error(client, "/groups/GK", metrics="saves,nope") == (404, "UNKNOWN_METRIC")
    assert error(client, "/groups/GK", metrics="saves", sort="goals") == (400, "BAD_REQUEST")
    assert error(client, "/groups/GK", metrics=" , ") == (400, "BAD_REQUEST")
    many = ",".join(list(BY_CODE)[: MAX_METRICS + 1])
    assert error(client, "/groups/GK", metrics=many) == (400, "BAD_REQUEST")
    assert error(client, "/groups/GK", scope="l_NOPE.1999") == (404, "SCOPE_NOT_FOUND")


def test_before_aggregation(api_db: str) -> None:
    assert cli("sync", "season", *SEASON_ARGS, "--offline", "--limit", "1").exit_code == 0
    with TestClient(create_app(api_db)) as client:
        assert error(client, "/groups/GK") == (409, "SCOPE_NOT_AGGREGATED")
        assert error(client, "/players/p_CST01/template") == (409, "SCOPE_NOT_AGGREGATED")
