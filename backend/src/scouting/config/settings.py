"""Runtime configuration, read from the environment (and `.env` files) in one place.

Variable names match the repo-root `.env.example`. Relative paths are resolved from the
directory commands run in, which is `backend/`.

The API key is a `SecretStr`, so it never shows up in `repr()`, logs or tracebacks.
It's optional at load time because offline commands (rebuilding scouting.db from
raw.db) don't need it; anything that calls the API uses `require_api_key()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class MissingApiKeyError(RuntimeError):
    """Raised when a command needs PitchAPI but PITCHAPI_API_KEY isn't set."""


class Settings(BaseSettings):
    # Real environment variables win over .env files; of the files, the later one wins.
    # "../.env" is the repo root when running from backend/, which is where ours lives.
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    pitchapi_api_key: SecretStr | None = Field(default=None, validation_alias="PITCHAPI_API_KEY")
    database_url: str = Field(default="sqlite:///data/scouting.db", validation_alias="DATABASE_URL")
    raw_db_path: Path = Field(default=Path("data/raw.db"), validation_alias="RAW_DB_PATH")
    # The burst limit is unpublished; the upper bound stops a typo from hammering the API.
    sync_max_rps: float = Field(default=1.0, gt=0, le=5, validation_alias="SYNC_MAX_RPS")
    log_level: LogLevel = Field(default="INFO", validation_alias="LOG_LEVEL")

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @property
    def min_request_interval_s(self) -> float:
        """What PitchApiClient's `min_interval_s` should be."""
        return 1.0 / self.sync_max_rps

    def require_api_key(self) -> str:
        if self.pitchapi_api_key is None or not self.pitchapi_api_key.get_secret_value():
            raise MissingApiKeyError(
                "PITCHAPI_API_KEY is not set. Put it in the repo-root .env "
                "(see .env.example) or export it."
            )
        return self.pitchapi_api_key.get_secret_value()