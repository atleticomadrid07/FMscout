"""Canonical tables (SCHEMA.md §4-§5). Slice 1b: competitions, seasons, teams, matches,
their source-ID maps, and sync state. Slice 1c: players, appearances, metrics, stat values
and formation roles.

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
from scouting.domain.positions import ROLES, SIDES
from scouting.metrics.registry import ABSENCE_POLICIES, AGGREGATIONS, APPLIES_TO
from scouting.metrics.registry import KINDS as METRIC_KINDS
from scouting.metrics.registry import SOURCES as METRIC_SOURCES

SOURCES: Final = ("pitchapi", "fm24")
PLAYER_ORIGINS: Final = ("real", "fm")
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


# --- players and appearances (slice 1c) -------------------------------------------------------


class Player(Base):
    __tablename__ = "player"
    __table_args__ = (CheckConstraint(_in("origin", PLAYER_ORIGINS), name="origin"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str] = mapped_column(String(8))  # 'real' | 'fm' (SCHEMA §4)
    name: Mapped[str] = mapped_column(String(200))
    short_name: Mapped[str | None] = mapped_column(String(100))  # from the profile (1d)
    search_name: Mapped[str] = mapped_column(String(200))  # unidecoded, lowercased
    country_code: Mapped[str | None] = mapped_column(String(16))
    shirt_number: Mapped[str | None] = mapped_column(String(8))  # the profile sends a string
    listed_slot_code: Mapped[int | None]  # profile position_id: display/fallback only
    image_url: Mapped[str | None] = mapped_column(String(500))
    profile_fetched_at: Mapped[datetime | None]


class PlayerSourceId(Base):
    __tablename__ = "player_source_id"
    __table_args__ = (
        CheckConstraint(_in("source", SOURCES), name="source"),
        Index(None, "player_id"),
    )

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"))


class Appearance(Base):
    """One player in one match. Exists only if /players gave the player minutes_played."""

    __tablename__ = "appearance"
    __table_args__ = (
        UniqueConstraint("match_id", "player_id"),
        CheckConstraint(f"role IS NULL OR {_in('role', ROLES)}", name="role"),
        CheckConstraint(f"side IS NULL OR {_in('side', SIDES)}", name="side"),
        CheckConstraint("minutes >= 0", name="minutes"),
        Index(None, "player_id"),
        Index(None, "team_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("match.id", ondelete="CASCADE"))
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"))
    is_starter: Mapped[bool | None]  # NULL when the lineups endpoint had no data
    slot_code: Mapped[int | None]  # starters: formation grid code, e.g. 38
    sub_category: Mapped[int | None]  # subs: 0 GK, 1 DEF, 2 MID, 3 ATT, -1 unclassified
    formation: Mapped[str | None] = mapped_column(String(16))  # the team's starting formation
    role: Mapped[str | None] = mapped_column(String(4))  # starters only, via formation_role
    side: Mapped[str | None] = mapped_column(String(1))
    is_goalkeeper: Mapped[bool]  # decides which metrics apply (registry `applies_to`)
    minutes: Mapped[int]  # canonical: /players minutes_played, full match = 90 (D-010)
    minutes_actual: Mapped[int | None]  # advanced minutes incl. stoppage; reference only
    rating: Mapped[float | None]  # rating_title; NULL = not rated (short cameo, or no ratings)


# --- metric registry and values -------------------------------------------------------------


class Metric(Base):
    """Synced from scouting.metrics.registry; `code` is the stable identity."""

    __tablename__ = "metric"
    __table_args__ = (
        CheckConstraint(_in("kind", METRIC_KINDS), name="kind"),
        CheckConstraint(_in("source", METRIC_SOURCES), name="source"),
        CheckConstraint(_in("absence_policy", ABSENCE_POLICIES), name="absence_policy"),
        CheckConstraint(_in("aggregation", AGGREGATIONS), name="aggregation"),
        CheckConstraint(_in("applies_to", APPLIES_TO), name="applies_to"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(32))
    source_key: Mapped[str] = mapped_column(String(100))
    absence_policy: Mapped[str] = mapped_column(String(32))
    aggregation: Mapped[str] = mapped_column(String(32))
    applies_to: Mapped[str] = mapped_column(String(16))
    per90_allowed: Mapped[bool]
    is_model_output: Mapped[bool]
    higher_is_better: Mapped[bool]
    description: Mapped[str] = mapped_column(String(500))


class StatValue(Base):
    """One metric for one appearance. A row with value NULL means unknown/undefined; no row
    means the metric doesn't apply or its source had no data for this match."""

    __tablename__ = "stat_value"
    __table_args__ = (
        CheckConstraint("denominator IS NULL OR denominator >= 0", name="denominator"),
        {"sqlite_with_rowid": False},
    )

    appearance_id: Mapped[int] = mapped_column(
        ForeignKey("appearance.id", ondelete="CASCADE"), primary_key=True
    )
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), primary_key=True)
    value: Mapped[float | None]  # successes for fractions
    denominator: Mapped[float | None]  # attempts, fractions only


class FormationRole(Base):
    """Synced from scouting.domain.positions (SCHEMA §7)."""

    __tablename__ = "formation_role"
    __table_args__ = (
        CheckConstraint(_in("role", ROLES), name="role"),
        CheckConstraint(_in("side", SIDES), name="side"),
    )

    formation: Mapped[str] = mapped_column(String(16), primary_key=True)
    slot_code: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(4))
    side: Mapped[str] = mapped_column(String(1))
