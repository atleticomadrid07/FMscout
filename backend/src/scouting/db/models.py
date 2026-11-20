"""Canonical tables for slice 1b (SCHEMA.md §4-§5): competitions, seasons, teams,
matches, their source-ID maps, and sync state. Players and stats arrive in 1c.

Conventions:
- Internal INTEGER surrogate keys everywhere; external IDs live only in *_source_id.
- Timestamps are stored as naive UTC (SQLite has no timezone type). Convert at the
  boundary with `to_utc_naive()`.
- Enumerated text columns are guarded by CHECK constraints.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from scouting.db.base import Base

SOURCES: Final = ("pitchapi", "fm24")
FETCH_STATUSES: Final = ("pending", "ok", "missing")
ADVANCED_STATUSES: Final = ("pending", "ok", "unavailable")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def to_utc_naive(value: datetime) -> datetime:
    """Normalize a timezone-aware datetime to the naive-UTC form we store."""
    if value.tzinfo is None:
        raise ValueError("expected a timezone-aware datetime")
    return value.astimezone(UTC).replace(tzinfo=None)


# --- reference ------------------------------------------------------------------------------


class Competition(Base):
    __tablename__ = "competition"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    country_code: Mapped[str] = mapped_column(String(16))
    image_url: Mapped[str | None] = mapped_column(String(500))


class CompetitionSourceId(Base):
    __tablename__ = "competition_source_id"
    __table_args__ = (
        CheckConstraint(_in("source", SOURCES), name="source"),
        Index(None, "competition_id"),
    )

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id", ondelete="CASCADE"))


class Season(Base):
    __tablename__ = "season"
    __table_args__ = (UniqueConstraint("competition_id", "label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(16))  # "2025/2026" or "2026", as PitchAPI sends it
    is_current: Mapped[bool]


class Team(Base):
    __tablename__ = "team"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    image_url: Mapped[str | None] = mapped_column(String(500))


class TeamSourceId(Base):
    __tablename__ = "team_source_id"
    __table_args__ = (
        CheckConstraint(_in("source", SOURCES), name="source"),
        Index(None, "team_id"),
    )

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"))


# --- matches ---------------------------------------------------------------------------------


class Match(Base):
    __tablename__ = "match"
    __table_args__ = (
        CheckConstraint("home_team_id <> away_team_id", name="distinct_teams"),
        CheckConstraint(_in("players_status", FETCH_STATUSES), name="players_status"),
        CheckConstraint(_in("advanced_status", ADVANCED_STATUSES), name="advanced_status"),
        CheckConstraint(_in("lineups_status", FETCH_STATUSES), name="lineups_status"),
        Index(None, "season_id", "kickoff_utc"),
        Index(None, "home_team_id"),
        Index(None, "away_team_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id", ondelete="CASCADE"))
    kickoff_utc: Mapped[datetime]  # naive UTC
    match_date: Mapped[date]  # the date PitchAPI lists the match under
    status: Mapped[str] = mapped_column(String(32))  # as PitchAPI sends it, e.g. "finished"
    home_team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    home_score: Mapped[int | None]
    away_score: Mapped[int | None]
    home_formation: Mapped[str | None] = mapped_column(String(16))  # filled from lineups (1c)
    away_formation: Mapped[str | None] = mapped_column(String(16))
    # server_default too, so rows inserted outside the ORM (plain SQL, data migrations)
    # still get 'pending' instead of failing NOT NULL.
    players_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    advanced_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    lineups_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    normalized_at: Mapped[datetime | None]  # naive UTC; set when per-match data is normalized


class MatchSourceId(Base):
    __tablename__ = "match_source_id"
    __table_args__ = (
        CheckConstraint(_in("source", SOURCES), name="source"),
        Index(None, "match_id"),
    )

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("match.id", ondelete="CASCADE"))


# --- sync ------------------------------------------------------------------------------------


class SyncState(Base):
    __tablename__ = "sync_state"

    season_id: Mapped[int] = mapped_column(
        ForeignKey("season.id", ondelete="CASCADE"), primary_key=True
    )
    # The fetched_at of the match list used, not wall-clock time: re-running a sync
    # from cache leaves the database byte-for-byte the same.
    match_list_fetched_at: Mapped[datetime | None]
    last_full_sync_at: Mapped[datetime | None]
