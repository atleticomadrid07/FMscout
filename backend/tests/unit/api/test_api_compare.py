"""Slice 3d: /compare and `min_minutes` on /stats, /template, /groups, over the hand-made
three-match fixture season (not real PitchAPI data). Fixture groups at the stored 45 min:
GK 4 (p_CGK01 180, p_BGK02 180, p_AGK01 90, p_AGK02 90; p_AGK03 5 min is outside),
FB 2, W 2 (p_BAM01, p_AAM01), ST 2 (p_CST01, p_AST01)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from scouting.api.app import create_app
from scouting.api.deps import read_only_engine
from scouting.api.ranking import custom_threshold
from scouting.cli.main import app as cli_app
from scouting.db.queries import find_scope
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


def players(body: Any) -> dict[str, Any]:
    return {p["id"]: p for p in body["players"]}


# --- /compare ----------------------------------------------------------------------------------


def test_compare_in_own_groups_with_the_first_players_template(client: TestClient) -> None:
    body = get(client, "/compare", players="p_CST01,p_BAM01")
    assert body["template"] == {"id": "builtin:ST", "label": "Striker", "peer_role": "ST"}
    assert [m["metric"] for m in body["metrics"]] == builtin_templates()["ST"].codes
    assert (body["against"], body["min_minutes"], body["stored"]) == (None, 45, True)
    assert [p["id"] for p in body["players"]] == ["p_CST01", "p_BAM01"]  # the order asked
    striker, winger = body["players"]
    assert (striker["group"]["role"], striker["outside_group"]) == ("ST", False)
    assert (winger["group"]["role"], winger["outside_group"]) == ("W", False)
    # Listed the other way round, the winger's template is used (user's choice, D-020).
    flipped = get(client, "/compare", players="p_BAM01,p_CST01")
    assert flipped["template"]["id"] == "builtin:W"


def test_compare_values_match_the_stored_numbers(client: TestClient) -> None:
    body = get(client, "/compare", players="p_CGK01,p_BGK02,p_AGK01", template="GK")
    codes = ",".join(m["metric"] for m in body["metrics"])
    for pid, player in players(body).items():
        lines = {
            s["metric"]: s for s in get(client, f"/players/{pid}/stats", metrics=codes)["stats"]
        }
        for code, value in player["values"].items():
            line = lines.get(code)
            if line is None:
                assert value["reason"] == "no data" and value["pct"] is None
                continue
            assert (value["pct"], value["ranked_among"], value["reason"]) == (
                line["pct"], line["ranked_among"], line["reason"],
            )  # fmt: skip
            expected = line["rate"] if line["basis"] == "rate" else line["per90"]
            assert value["value"] == expected and value["minutes"] == line["minutes"]


def test_compare_against_another_group(client: TestClient) -> None:
    body = get(client, "/compare", players="p_BAM01,p_AAM01,p_CST01", against="W")
    assert body["against"] == "W" and body["template"]["id"] == "builtin:W"
    by_id = players(body)
    striker = by_id["p_CST01"]
    assert striker["outside_group"] is True and striker["peer_role"] == "ST"
    assert striker["group"]["role"] == "W" and striker["group"]["size"] == 2
    npxg = striker["values"]["npxg"]
    assert npxg["pct"] == 100.0 and npxg["ranked_among"] == 2  # above both wingers, not added
    # The wingers' own percentiles don't move: the same as /groups/W.
    group = {p["id"]: p for p in get(client, "/groups/W", metrics="npxg,shots")["players"]}
    for pid in ("p_BAM01", "p_AAM01"):
        assert by_id[pid]["outside_group"] is False
        for code in ("npxg", "shots"):
            assert by_id[pid]["values"][code]["pct"] == group[pid]["values"][code]["pct"]


def test_compare_a_player_under_the_minutes(client: TestClient) -> None:
    body = get(client, "/compare", players="p_CGK01,p_AGK03", metrics="saves,goals_conceded")
    assert body["template"] is None
    backup = players(body)["p_AGK03"]  # a keeper with 5 minutes: outside his own group
    assert backup["outside_group"] is True and backup["group"]["role"] == "GK"
    assert backup["values"]["saves"]["pct"] is None
    assert backup["values"]["saves"]["reason"] == "5 min with data (needs 45)"  # floor capped


def test_compare_a_player_without_a_role(client: TestClient) -> None:
    body = get(client, "/compare", players="p_CST01,p_ASB01")
    sub = players(body)["p_ASB01"]
    assert sub["group"] is None and sub["outside_group"] is False and sub["peer_role"] is None
    assert {v["reason"] for v in sub["values"].values()} <= {"no peer role", "no data"}
    assert error(client, "/compare", players="p_ASB01,p_CST01") == (400, "TEMPLATE_REQUIRED")
    ranked = get(client, "/compare", players="p_ASB01,p_CST01", against="ST", template="ST")
    assert players(ranked)["p_ASB01"]["outside_group"] is True


def test_compare_at_another_threshold(client: TestClient) -> None:
    body = get(client, "/compare", players="p_CGK01,p_AGK01", metrics="saves", min_minutes=100)
    assert (body["min_minutes"], body["stored"]) == (100, False)
    regular, short = body["players"]
    assert regular["group"] == {
        "role": "GK", "size": 2, "min_minutes": 100, "small_sample": True, "stored": False,
    }  # fmt: skip
    assert regular["outside_group"] is False and regular["values"]["saves"]["ranked_among"] == 2
    # 90 minutes: under the new threshold, ranked against the two members from outside.
    assert short["outside_group"] is True and short["values"]["saves"]["pct"] is not None
    assert short["values"]["saves"]["ranked_among"] == 2
    empty = get(client, "/compare", players="p_CGK01,p_AGK01", metrics="saves", min_minutes=99_999)
    assert all(p["group"] is None for p in empty["players"])
    assert (
        empty["players"][0]["values"]["saves"]["reason"] == "no GK players with at least 99999 min"
    )


def test_compare_errors(client: TestClient) -> None:
    assert error(client, "/compare", players="p_CST01") == (400, "BAD_REQUEST")
    seven = ",".join(["p_CST01", "p_AST01", "p_CGK01", "p_BGK02", "p_AGK01", "p_AGK02", "p_BAM01"])
    assert error(client, "/compare", players=seven) == (400, "BAD_REQUEST")
    assert error(client, "/compare", players="p_CST01,p_CST01") == (400, "BAD_REQUEST")
    both = {"metrics": "shots", "template": "ST"}
    assert error(client, "/compare", players="p_CST01,p_AST01", **both) == (400, "BAD_REQUEST")
    assert error(client, "/compare", players="p_CST01,p_AST01", against="XX") == (
        400,
        "UNKNOWN_ROLE",
    )
    assert error(client, "/compare", players="p_CST01,p_NOPE") == (404, "PLAYER_NOT_FOUND")
    assert error(client, "/compare", players="p_CST01,p_AST01", metrics="nope") == (
        404,
        "UNKNOWN_METRIC",
    )
    assert error(client, "/compare", players="p_CST01,p_AST01", template="XX") == (
        404,
        "UNKNOWN_TEMPLATE",
    )
    assert error(client, "/compare", players="p_CST01,p_AST01", min_minutes=89) == (
        400,
        "BAD_REQUEST",
    )
    assert error(client, "/compare") == (400, "BAD_REQUEST")  # players is required


# --- min_minutes on /stats, /template, /groups ----------------------------------------------------


def test_stats_at_another_threshold(client: TestClient) -> None:
    body = get(client, "/players/p_CGK01/stats", metrics="saves,goals_conceded", min_minutes=100)
    assert body["group"] == {
        "role": "GK", "size": 2, "min_minutes": 100, "small_sample": True, "stored": False,
    }  # fmt: skip
    assert all(s["ranked_among"] == 2 for s in body["stats"])
    short = get(client, "/players/p_AGK01/stats", metrics="saves", min_minutes=100)
    assert short["group"] is None
    assert short["stats"][0]["reason"] == "not ranked: below 100 min"
    assert get(client, "/players/p_CGK01/stats", metrics="saves")["group"]["stored"] is True


def test_template_at_another_threshold(client: TestClient) -> None:
    body = get(client, "/players/p_CGK01/template", min_minutes=100)
    assert body["group"]["size"] == 2 and body["group"]["stored"] is False
    rows = [r for s in body["sections"] for r in s["rows"]]
    assert all(r["ranked_among"] in (None, 1, 2) for r in rows)


def test_groups_at_another_threshold(client: TestClient) -> None:
    body = get(client, "/groups/GK", metrics="saves", min_minutes=100)
    assert body["group"]["size"] == 2 and body["group"]["stored"] is False
    assert {p["id"] for p in body["players"]} == {"p_CGK01", "p_BGK02"}
    assert [p["values"]["saves"]["pct"] for p in body["players"]] == [75.0, 25.0]
    assert body["metrics"][0]["ranked_among"] == 2
    assert error(client, "/groups/GK", min_minutes=99_999) == (404, "GROUP_NOT_FOUND")
    assert error(client, "/groups/GK", min_minutes=10) == (400, "BAD_REQUEST")


def test_the_stored_threshold_uses_the_stored_numbers(api_db: str, client: TestClient) -> None:
    engine = read_only_engine(api_db)
    with Session(engine) as session:
        scope = find_scope(session, KEY)
    engine.dispose()
    assert scope is not None and scope.min_minutes == 45
    assert custom_threshold(scope, None) is None and custom_threshold(scope, 45) is None
    assert custom_threshold(scope, 100) == 100
    assert custom_threshold(replace(scope, min_minutes=855), 855) is None


def test_before_aggregation(api_db: str) -> None:
    assert cli("sync", "season", *SEASON_ARGS, "--offline", "--limit", "1").exit_code == 0
    with TestClient(create_app(api_db)) as client:
        assert error(client, "/compare", players="p_CST01,p_AST01") == (409, "SCOPE_NOT_AGGREGATED")
