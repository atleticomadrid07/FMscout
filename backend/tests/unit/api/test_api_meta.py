"""Slice 3a: the API skeleton: health, scopes, metrics, templates, errors, read-only."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from scouting.api.app import create_app
from scouting.api.deps import read_only_engine, read_only_url
from scouting.cli.main import app as cli_app
from scouting.db.schema_version import head_revision

KEY = "l_FIXT01.2025-2026"
SEASON_ARGS = ("--league", "l_FIXT01", "--season", "2025/2026")
OPENAPI_SNAPSHOT = Path(__file__).with_name("openapi.json")
runner = CliRunner()


def cli(*args: str) -> Any:
    """Run a `scouting` command against the test databases (api_db sets the environment)."""
    return runner.invoke(cli_app, list(args))


@pytest.fixture
def client(api_db: str) -> Iterator[TestClient]:
    """The API over the fixture season, synced and aggregated (offline)."""
    result = cli("sync", "season", *SEASON_ARGS, "--offline")
    assert result.exit_code == 0, result.output
    with TestClient(create_app(api_db)) as test_client:
        yield test_client


def error(response: Any) -> tuple[int, str]:
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    return response.status_code, body["error"]["code"]


# --- health and the database -------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    body = client.get("/api/v1/health").json()
    assert body == {
        "status": "ok", "api_version": "1", "schema_revision": head_revision(), "message": None,
    }  # fmt: skip


def test_an_unmigrated_database_answers_503(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    (tmp_path / "empty.db").touch()
    with TestClient(create_app(url)) as client:
        health = client.get("/api/v1/health").json()
        assert health["status"] == "schema_out_of_date" and health["message"]
        assert error(client.get("/api/v1/scopes")) == (503, "SCHEMA_OUT_OF_DATE")
        assert client.get("/api/v1/metrics").status_code == 200  # no database needed


def test_a_missing_database_answers_503(tmp_path: Path) -> None:
    with TestClient(create_app(f"sqlite:///{tmp_path / 'nope.db'}")) as client:
        assert client.get("/api/v1/health").json()["status"] == "schema_out_of_date"
        assert error(client.get("/api/v1/scopes")) == (503, "SCHEMA_OUT_OF_DATE")
    assert not (tmp_path / "nope.db").exists()  # read-only: never creates the file


def test_read_only_url() -> None:
    url = read_only_url("sqlite:///data/scouting.db")
    assert url.startswith("sqlite:///file:/") and url.endswith("scouting.db?mode=ro&uri=true")
    assert read_only_url("sqlite:///:memory:") == "sqlite:///:memory:"
    assert read_only_url("postgresql://u@h/db") == "postgresql://u@h/db"


def test_the_api_cannot_write(api_db: str) -> None:
    engine = read_only_engine(api_db)
    with engine.connect() as connection, pytest.raises(OperationalError, match="readonly"):
        connection.execute(text("DELETE FROM metric"))
    engine.dispose()


def test_requests_change_nothing(client: TestClient) -> None:
    before = cli("report", "checksum").output
    for path in ("/health", "/scopes", f"/scopes/{KEY}", "/metrics", "/templates"):
        assert client.get(f"/api/v1{path}").status_code == 200
    params = {"scope": KEY, "q": "keeper", "metric": "shots"}  # slice 3b's routes
    for path in ("/search", "/p_CST01", "/p_CST01/stats", "/p_CST01/matches", "/p_CST01/template"):
        assert client.get(f"/api/v1/players{path}", params=params).status_code == 200
    assert client.get("/api/v1/groups/GK", params={"scope": KEY}).status_code == 200  # 3c
    for extra in ({}, {"min_minutes": 100}, {"against": "W"}):  # 3d
        compare = {"scope": KEY, "players": "p_CST01,p_CGK01", **extra}
        assert client.get("/api/v1/compare", params=compare).status_code == 200
    custom = {"scope": KEY, "min_minutes": 100}
    assert client.get("/api/v1/groups/GK", params=custom).status_code == 200
    assert cli("report", "checksum").output == before


# --- scopes ------------------------------------------------------------------------------------


def test_scopes(client: TestClient) -> None:
    scopes = client.get("/api/v1/scopes").json()["scopes"]
    assert [s["key"] for s in scopes] == [KEY]
    scope = scopes[0]
    assert scope["season"] == "2025/2026" and scope["source"] == "pitchapi"
    assert scope["aggregated"] and scope["min_minutes"] == 45 and scope["computed_at"]
    assert scope["coverage"]["matches"] == 3 and scope["coverage"]["pending"] == 0
    assert [(g["role"], g["size"], g["small_sample"]) for g in scope["peer_groups"]] == [
        ("GK", 4, True), ("FB", 2, True), ("W", 2, True), ("ST", 2, True),
    ]  # fmt: skip
    assert client.get(f"/api/v1/scopes/{KEY}").json() == scope


def test_scope_before_aggregation(api_db: str) -> None:
    assert cli("sync", "season", *SEASON_ARGS, "--offline", "--limit", "1").exit_code == 0
    with TestClient(create_app(api_db)) as client:
        scope = client.get(f"/api/v1/scopes/{KEY}").json()
    assert not scope["aggregated"] and scope["peer_groups"] == []
    assert scope["min_minutes"] is None and scope["computed_at"] is None
    assert scope["coverage"]["pending"] == 2  # two of three matches not synced yet


def test_unknown_scope(client: TestClient) -> None:
    assert error(client.get("/api/v1/scopes/l_NOPE.1999")) == (404, "SCOPE_NOT_FOUND")


# --- metrics and templates ---------------------------------------------------------------------


def test_metrics(client: TestClient) -> None:
    metrics = client.get("/api/v1/metrics").json()["metrics"]
    by_code = {m["code"]: m for m in metrics}
    assert len(metrics) == 100 and metrics[-1]["code"] == "aerials_won_count"
    assert by_code["dribbles_completed_count"]["volume_of"] == "dribbles_completed"
    assert by_code["dribbles_completed"]["basis"] == "rate"
    assert by_code["npxg"]["model"] and by_code["npxg"]["basis"] == "per90"
    assert by_code["goals"]["outcome"] and by_code["miscontrols"]["direction"] == "lower"
    assert by_code["passes_completed"]["min_attempts"] == 100
    assert client.get("/api/v1/metrics/xgot").json()["code"] == "xgot"
    assert error(client.get("/api/v1/metrics/nope")) == (404, "UNKNOWN_METRIC")


def test_templates(client: TestClient) -> None:
    templates = client.get("/api/v1/templates").json()["templates"]
    assert [t["peer_role"] for t in templates] == ["GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"]
    st = client.get("/api/v1/templates/builtin:ST").json()
    assert st["size"] == 12 and st["builtin"]
    assert [s["name"] for s in st["sections"]] == ["Shooting", "Link-up", "Aerial", "Pressing"]
    assert error(client.get("/api/v1/templates/builtin:LW")) == (404, "UNKNOWN_TEMPLATE")


# --- errors, CORS, the contract -------------------------------------------------------------------


def test_error_envelope_for_unknown_routes_and_methods(client: TestClient) -> None:
    assert error(client.get("/api/v1/nope")) == (404, "NOT_FOUND")
    assert error(client.post("/api/v1/scopes")) == (405, "METHOD_NOT_ALLOWED")


def test_cors_allows_the_vite_dev_server_only(client: TestClient) -> None:
    allowed = client.get("/api/v1/metrics", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.get("/api/v1/metrics", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in other.headers


def test_openapi_snapshot() -> None:
    """The contract. If this fails after a deliberate API change, regenerate it with
    UPDATE_OPENAPI=1 uv run pytest tests/unit/api/test_api_meta.py -k openapi
    and review the diff of openapi.json like code."""
    current = create_app("sqlite:///:memory:").openapi()
    if os.environ.get("UPDATE_OPENAPI") == "1":
        OPENAPI_SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
    assert current == json.loads(OPENAPI_SNAPSHOT.read_text())


def test_serve_binds_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    result = cli("serve", "--port", "8123")
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:8123/docs" in result.output
    ((args, kwargs),) = calls
    assert args == ("scouting.api.app:create_app",)
    assert kwargs == {"factory": True, "host": "127.0.0.1", "port": 8123, "reload": False}
