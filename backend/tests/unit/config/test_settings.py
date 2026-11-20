"""Unit tests for Settings. `_env_file=None` keeps the real .env (and real key) out of tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from scouting.config.settings import MissingApiKeyError, Settings

FAKE_KEY = "pk_test_not_a_real_key"
ENV_VARS = ("PITCHAPI_API_KEY", "DATABASE_URL", "RAW_DB_PATH", "SYNC_MAX_RPS", "LOG_LEVEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def load() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_reads_api_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITCHAPI_API_KEY", FAKE_KEY)
    assert load().require_api_key() == FAKE_KEY


def test_api_key_never_appears_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITCHAPI_API_KEY", FAKE_KEY)
    settings = load()
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings.model_dump())


@pytest.mark.parametrize("value", [None, ""])
def test_require_api_key_missing_raises(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is not None:
        monkeypatch.setenv("PITCHAPI_API_KEY", value)
    with pytest.raises(MissingApiKeyError):
        load().require_api_key()


def test_defaults_match_env_example() -> None:
    settings = load()
    assert settings.database_url == "sqlite:///data/scouting.db"
    assert settings.raw_db_path == Path("data/raw.db")
    assert settings.sync_max_rps == 1.0
    assert settings.min_request_interval_s == 1.0
    assert settings.log_level == "INFO"


def test_reads_all_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///elsewhere.db")
    monkeypatch.setenv("RAW_DB_PATH", str(tmp_path / "raw.db"))
    monkeypatch.setenv("SYNC_MAX_RPS", "0.5")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    settings = load()
    assert settings.database_url == "sqlite:///elsewhere.db"
    assert settings.raw_db_path == tmp_path / "raw.db"
    assert settings.min_request_interval_s == 2.0
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize("rps", ["0", "-1", "6", "fast"])
def test_invalid_sync_max_rps_raises(monkeypatch: pytest.MonkeyPatch, rps: str) -> None:
    monkeypatch.setenv("SYNC_MAX_RPS", rps)
    with pytest.raises(ValidationError):
        load()


def test_invalid_log_level_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        load()


def test_reads_dotenv_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"PITCHAPI_API_KEY={FAKE_KEY}\nSYNC_MAX_RPS=2\nUNRELATED=1\n")
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.require_api_key() == FAKE_KEY
    assert settings.sync_max_rps == 2.0
