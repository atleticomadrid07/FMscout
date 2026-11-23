"""Slice 3b: /players/search, the player card, the stat table and per-match values, over the
hand-made three-match fixture season (not real PitchAPI data)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from scouting.api.app import create_app
from scouting.api.routes.players import _six_or_eight
from scouting.cli.main import app as cli_app
from scouting.db.models import Match, MatchSourceId, Player, PlayerSourceId
from scouting.db.queries import GroupInfo, PlayerSeason
from scouting.domain.names import search_name

KEY = "l_FIXT01.2025-2026"
SEASON_ARGS = ("--league", "l_FIXT01", "--season", "2025/2026")
runner = CliRunner()


def cli(*args: str) -> Any:
    return runner.invoke(cli_app, list(args))


def synced(api_db: str) -> None:
    result = cli("sync", "season", *SEASON_ARGS, "--offline")
    assert result.exit_code == 0, result.output


@pytest.fixture
def client(api_db: str) -> Iterator[TestClient]:
    synced(api_db)
    with TestClient(create_app(api_db)) as test_client:
        yield test_client


def get(client: TestClient, path: str, **params: Any) -> Any:
    response = client.get(f"/api/v1{path}", params={"scope": KEY, **params})
    assert response.status_code == 200, response.text
    return response.json()


def error(response: Any) -> tuple[int, str]:
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    return response.status_code, body["error"]["code"]


# --- search ------------------------------------------------------------------------------------


def test_search_exact_name_then_more_minutes_first(client: TestClient) -> None:
    body = get(client, "/players/search", q="Ada Keeper")
    assert body["query"] == "Ada Keeper" and body["scope"] == KEY
    hits = body["results"]
    # Two fixture keepers share the name (one per team): exact, more minutes first.
    assert [(h["id"], h["exact"], h["minutes"]) for h in hits[:2]] == [
        ("p_CGK01", True, 180), ("p_AGK01", True, 90),
    ]  # fmt: skip
    assert all(not h["exact"] and h["score"] < 100 for h in hits[2:])
    assert hits[0]["team"] == "Placeholder Town" and hits[0]["peer_role"] == "GK"


def test_search_short_name_surname_and_accents(client: TestClient) -> None:
    assert [h["id"] for h in get(client, "/players/search", q="c. striker")["results"]] == [
        "p_CST01", "p_AST01",
    ]  # fmt: skip
    surname = get(client, "/players/search", q="striker")["results"]
    assert {h["score"] for h in surname} == {97.0}
    accent = get(client, "/players/search", q="fullback")["results"]  # stored as Fullbäck
    assert [h["id"] for h in accent] == ["p_CLB01", "p_ALB01"]


def test_search_filters_and_limit(client: TestClient) -> None:
    def found(**params: Any) -> list[str]:
        return [h["id"] for h in get(client, "/players/search", **params)["results"]]

    assert found(q="keeper", team="sample") == ["p_BGK02"]
    assert found(q="keeper", min_minutes=100) == ["p_CGK01", "p_BGK02"]
    assert found(q="keeper", role="FB") == []
    assert len(found(q="keeper", limit=1)) == 1
    assert found(q="nobody like this") == []


def test_search_validation(client: TestClient) -> None:
    def call(**params: Any) -> tuple[int, str]:
        return error(client.get("/api/v1/players/search", params={"scope": KEY, **params}))

    assert call(q="a") == (400, "BAD_REQUEST")
    assert call(q="keeper", limit=51) == (400, "BAD_REQUEST")
    assert call(q="keeper", role="XX") == (400, "UNKNOWN_ROLE")
    assert error(client.get("/api/v1/players/search", params={"q": "ab"})) == (400, "BAD_REQUEST")


# --- the player card ---------------------------------------------------------------------------


def test_player_card(client: TestClient) -> None:
    card = get(client, "/players/p_CST01")
    assert (card["id"], card["name"], card["short_name"]) == (
        "p_CST01",
        "Cal Striker",
        "C. Striker",
    )
    assert card["provenance"] == {
        "source": "pitchapi", "scope": KEY, "competition": "Fixture League",
        "season": "2025/2026", "computed_at": card["provenance"]["computed_at"],
    }  # fmt: skip
    assert (card["minutes"], card["matches"], card["starts"]) == (156, 2, 2)
    assert card["teams"] == [{"name": "Placeholder Town", "minutes": 156, "matches": 2}]
    assert (card["primary_role"], card["role_source"], card["peer_role"]) == ("ST", "starts", "ST")
    assert card["starts_by_role"] == [{"role": "ST", "minutes": 156, "starts": 2}]
    assert card["six_or_eight"] is None  # not a central mid
    assert card["rating"]["rated_matches"] == 2
    assert card["group"] == {
        "role": "ST", "size": 2, "min_minutes": 45, "small_sample": True, "stored": True,
    }  # fmt: skip
    assert card["not_ranked_reason"] is None
    assert card["coverage"] == {"matches": 2, "without_advanced": 0, "without_heatmap": 0}


def test_an_unranked_player_says_why(client: TestClient) -> None:
    sub = get(client, "/players/p_ASB01")  # 12 minutes off the bench, no start: no role
    assert sub["group"] is None and sub["peer_role"] is None
    assert sub["not_ranked_reason"] == "no peer role"
    backup = get(client, "/players/p_AGK03")  # a keeper with 5 minutes
    assert backup["group"] is None and backup["peer_role"] == "GK"
    assert backup["not_ranked_reason"] == "not ranked: below 45 min"


def test_card_coverage_counts_matches_without_data(api_db: str) -> None:
    synced(api_db)
    engine = create_engine(api_db)
    with Session(engine) as session, session.begin():
        match_id = session.scalar(
            MatchSourceId.__table__.select()
            .with_only_columns(MatchSourceId.match_id)
            .where(MatchSourceId.external_id == "m_FIXT03")
        )
        session.execute(
            update(Match)
            .where(Match.id == match_id)
            .values(advanced_status="unavailable", heatmaps_status="missing")
        )
    engine.dispose()
    with TestClient(create_app(api_db)) as client:
        coverage = get(client, "/players/p_CST01")["coverage"]
    assert coverage == {"matches": 2, "without_advanced": 1, "without_heatmap": 1}


def test_player_errors(client: TestClient) -> None:
    def call(path: str, **params: Any) -> tuple[int, str]:
        return error(client.get(f"/api/v1{path}", params=params))

    assert call("/players/p_NOPE", scope=KEY) == (404, "PLAYER_NOT_FOUND")
    assert call("/players/p_CST01", scope="l_NOPE.1999") == (404, "SCOPE_NOT_FOUND")
    assert call("/players/p_CST01") == (400, "BAD_REQUEST")  # scope is required
    for path in ("/players/p_NOPE/stats", "/players/p_NOPE/matches"):
        assert call(path, scope=KEY, metric="shots") == (404, "PLAYER_NOT_FOUND")


def test_a_player_from_another_season(api_db: str) -> None:
    synced(api_db)
    engine = create_engine(api_db)
    with Session(engine) as session, session.begin():
        # Test-only: a player in the database with no appearance in this scope.
        player = Player(origin="real", name="Elsewhere Only", search_name=search_name("x"))
        session.add(player)
        session.flush()
        session.add(PlayerSourceId(source="pitchapi", external_id="p_ZZOTHER", player_id=player.id))
    engine.dispose()
    with TestClient(create_app(api_db)) as client:
        response = client.get("/api/v1/players/p_ZZOTHER", params={"scope": KEY})
    assert error(response) == (404, "PLAYER_NOT_IN_SCOPE")


def test_a_scope_before_aggregation(api_db: str) -> None:
    assert cli("sync", "season", *SEASON_ARGS, "--offline", "--limit", "1").exit_code == 0
    with TestClient(create_app(api_db)) as client:
        for path in ("/players/search", "/players/p_CST01", "/players/p_CST01/stats"):
            response = client.get(f"/api/v1{path}", params={"scope": KEY, "q": "cal"})
            assert error(response) == (409, "SCOPE_NOT_AGGREGATED")


# --- the 6/8 block (no central mids in the fixture season: built directly) --------------------


def central_mid(**overrides: Any) -> PlayerSeason:
    base: dict[str, Any] = {
        "name": "Test Mid", "external_id": "p_TEST", "competition": "C", "season": "S",
        "minutes": 900, "matches": 10, "starts": 10, "primary_role": "DM", "peer_role": "DM",
        "role_source": "starts", "avg_rating": None, "rated_matches": 0, "teams": [],
        "roles": [], "stats": [], "group": GroupInfo("DM", 20, 900), "group_min_minutes": 900,
        "peer_method": "depth", "depth": 10.5 / 105, "depth_starts": 9,
        "partner_gap": 1.05 / 105, "partner_starts": 6,
    }  # fmt: skip
    return PlayerSeason(**{**base, **overrides})


def test_six_or_eight_block() -> None:
    block = _six_or_eight(central_mid())
    assert block is not None
    assert (block.group, block.method, block.depth_m, block.partner_gap_m) == (
        "DM",
        "depth",
        10.5,
        1.05,
    )
    assert (block.depth_starts, block.partner_starts, block.borderline) == (9, 6, False)
    assert (block.cut_m, block.partner_gap_rule_m, block.min_starts) == (14.0, 2.0, 5)
    near = _six_or_eight(central_mid(depth=13.5 / 105, peer_role="DM"))
    assert near is not None and near.borderline
    fallback = _six_or_eight(
        central_mid(peer_method="formation", depth=None, depth_starts=None, partner_gap=None)
    )
    assert fallback is not None and fallback.depth_m is None and fallback.depth_starts == 0
    assert _six_or_eight(central_mid(peer_role="ST")) is None
    assert _six_or_eight(central_mid(peer_method=None)) is None


# --- the stat table ----------------------------------------------------------------------------


def test_stat_table(client: TestClient) -> None:
    body = get(client, "/players/p_CST01/stats")
    assert body["player"] == {"id": "p_CST01", "name": "Cal Striker"}
    assert body["minutes"] == 156 and body["group"]["role"] == "ST" and body["missing"] == []
    lines = {line["metric"]: line for line in body["stats"]}
    goals = lines["goals"]
    assert (goals["basis"], goals["total"], goals["minutes"], goals["matches"]) == (
        "per90",
        2.0,
        156,
        2,
    )
    assert goals["per90"] == pytest.approx(2 * 90 / 156)
    assert goals["rate"] is None and goals["rate_scale"] is None and goals["attempts"] is None
    assert goals["pct"] is not None and goals["ranked_among"] == 2 and goals["reason"] is None
    assert lines["xg"]["model"] and not goals["model"]
    passes = lines["passes_completed"]
    assert (passes["basis"], passes["rate_scale"]) == ("rate", "share")
    assert passes["rate"] == pytest.approx(passes["total"] / passes["attempts"])
    assert passes["pct"] is None and passes["reason"].startswith("needs 100 attempts")


def test_stat_table_rate_scales(client: TestClient) -> None:
    body = get(client, "/players/p_CGK01/stats", metrics="distribution_accuracy,avg_pass_length")
    accuracy, length = body["stats"]
    assert (accuracy["rate_scale"], length["rate_scale"]) == ("percent", "metres")
    # Averaged rates: weighted by the passes counted; no meaningful total.
    assert accuracy["total"] is None and accuracy["attempts"] == 60
    assert accuracy["rate"] == pytest.approx(80.0) and length["rate"] == pytest.approx(31.5)


def test_stat_table_chosen_metrics_keep_their_order(client: TestClient) -> None:
    body = get(client, "/players/p_CGK01/stats", metrics="saves, xg,goals")
    assert [line["metric"] for line in body["stats"]] == ["saves"]
    assert body["missing"] == ["xg", "goals"]  # outfield metrics: a keeper has no line


def test_stat_table_unknown_metric(client: TestClient) -> None:
    response = client.get(
        "/api/v1/players/p_CST01/stats", params={"scope": KEY, "metrics": "goals,nope"}
    )
    assert error(response) == (404, "UNKNOWN_METRIC")
    assert "nope" in response.json()["error"]["message"]


def test_an_unranked_players_lines_say_why(client: TestClient) -> None:
    lines = get(client, "/players/p_AGK03/stats")["stats"]
    assert lines and all(line["pct"] is None for line in lines)
    assert {line["reason"] for line in lines} == {"not ranked: below 45 min"}


# --- per-match values --------------------------------------------------------------------------


def test_matches(client: TestClient) -> None:
    body = get(client, "/players/p_CST01/matches", metric="shots")
    assert (body["metric"], body["stored_metric"], body["basis"]) == ("shots", "shots", "per90")
    rows = body["matches"]
    assert [r["match_id"] for r in rows] == ["m_FIXT02", "m_FIXT03"]  # oldest first
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
    first = rows[0]
    assert (first["team"], first["opponent"], first["venue"]) == (
        "Placeholder Town",
        "Sample Rovers",
        "away",
    )
    assert (first["minutes"], first["started"]) == (78, True)
    assert sum(r["value"] for r in rows) == pytest.approx(
        get(client, "/players/p_CST01/stats", metrics="shots")["stats"][0]["total"]
    )
    assert (first["goals_for"], first["goals_against"]) == (0, 0)
    home = rows[1]
    assert home["venue"] == "home" and home["opponent"] == "Example Athletic"
    assert (home["goals_for"], home["goals_against"]) == (1, 3)  # from his team's side
    assert all(r["attempts"] is None and r["rate"] is None for r in rows)


def test_matches_for_a_success_rate_and_its_volume(client: TestClient) -> None:
    rate = get(client, "/players/p_CST01/matches", metric="passes_completed")
    assert (rate["basis"], rate["rate_scale"]) == ("rate", "share")
    for r in rate["matches"]:
        assert r["attempts"] and r["rate"] == pytest.approx(r["value"] / r["attempts"])
    volume = get(client, "/players/p_CST01/matches", metric="passes_completed_count")
    assert (volume["stored_metric"], volume["basis"]) == ("passes_completed", "per90")
    assert [r["value"] for r in volume["matches"]] == [r["value"] for r in rate["matches"]]


def test_matches_for_an_averaged_rate(client: TestClient) -> None:
    body = get(client, "/players/p_CGK01/matches", metric="distribution_accuracy")
    assert body["rate_scale"] == "percent"
    assert all(r["value"] is None and r["rate"] is not None for r in body["matches"])
    assert all(0 <= r["rate"] <= 100 for r in body["matches"])


def test_matches_keep_appearances_without_data(client: TestClient) -> None:
    rows = get(client, "/players/p_CST01/matches", metric="saves")["matches"]  # a keeper metric
    assert len(rows) == 2 and all(r["value"] is None for r in rows)


def test_matches_errors(client: TestClient) -> None:
    def call(**params: Any) -> tuple[int, str]:
        return error(client.get("/api/v1/players/p_CST01/matches", params={"scope": KEY, **params}))

    assert call() == (400, "BAD_REQUEST")  # metric is required
    assert call(metric="nope") == (404, "UNKNOWN_METRIC")
