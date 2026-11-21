"""Canonical tables (SCHEMA.md §4-§6). Slice 1b: competitions, seasons, teams, matches,
their source-ID maps, and sync state. Slice 1c: players, appearances, metrics, stat values
and formation roles. Slice 2a: aggregate scopes and per-player season totals.

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
from scouting.domain.positions import PEER_ROLES, ROLES, SIDES
from scouting.metrics.registry import ABSENCE_POLICIES, AGGREGATIONS, APPLIES_TO
from scouting.metrics.registry import KINDS as METRIC_KINDS
from scouting.metrics.registry import SOURCES as METRIC_SOURCES

SOURCES: Final = ("pitchapi", "fm24")
PLAYER_ORIGINS: Final = ("real", "fm")
FETCH_STATUSES: Final = ("pending", "ok", "missing")
ADVANCED_STATUSES: Final = ("pending", "ok", "unavailable")
SCOPE_KINDS: Final = ("season", "fm_import")
PERCENTILE_BASES: Final = ("per90", "rate")
ROLE_SOURCES: Final = ("starts", "listed")
# How a central midfielder's 6/8 group was decided (D-018; metrics/depth.py).
PEER_METHODS: Final = ("depth", "partner", "formation")


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
    # Slice 2d: 'pending' (not fetched yet, e.g. an offline run), 'ok' or 'missing'. No CHECK:
    # SQLite can't add one to an existing table without rebuilding it, and `appearance`
    # references `match` (ERRORS.md); the values are FETCH_STATUSES, set only by the sync.
    heatmaps_status: Mapped[str] = mapped_column(
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
    # Slice 2d: where on the pitch the player's actions were, from the match heatmap.
    # depth = actions-weighted mean along the pitch, 0 = own goal line, 1 = opponent's;
    # depth_actions = how many actions it rests on. NULL: no heatmap, or no actions.
    depth: Mapped[float | None]
    depth_actions: Mapped[int | None]


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
    # Advanced fractions given as two fields: the attempts/weight path (slice 1d).
    denominator_key: Mapped[str | None] = mapped_column(String(100))
    # True: the stored value is a per-match rate x denominator (weighted average later).
    value_is_rate: Mapped[bool] = mapped_column(default=False, server_default="0")
    absence_policy: Mapped[str] = mapped_column(String(32))
    aggregation: Mapped[str] = mapped_column(String(32))
    applies_to: Mapped[str] = mapped_column(String(16))
    per90_allowed: Mapped[bool]
    is_model_output: Mapped[bool]
    higher_is_better: Mapped[bool]
    description: Mapped[str] = mapped_column(String(500))
    # Percentile eligibility and colouring (slice 2b, D-014). No CHECK on `direction`: SQLite
    # can't add one to an existing table without rebuilding it; the registry is validated by
    # tests instead. Plain string default, per the D-013 migration rule.
    min_attempts: Mapped[int | None]  # fractions: fewest season attempts for a percentile
    direction: Mapped[str] = mapped_column(String(8), server_default="higher")


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


# --- aggregates (Phase 2, slice 2a; SCHEMA §6) -----------------------------------------------
#
# Rebuilt per scope in one transaction (delete, then insert) by scouting.metrics.aggregate.
# Nothing here is edited by hand, and nothing reads stat_value at request time.


class Scope(Base):
    """What an aggregate is computed over: one competition-season, or (Phase 6) one FM import.
    Real seasons and FM snapshots share the aggregate tables through this."""

    __tablename__ = "scope"
    __table_args__ = (
        CheckConstraint(_in("kind", SCOPE_KINDS), name="kind"),
        CheckConstraint(
            "(kind = 'season' AND season_id IS NOT NULL AND fm_import_id IS NULL)"
            " OR (kind = 'fm_import' AND fm_import_id IS NOT NULL AND season_id IS NULL)",
            name="one_target",
        ),
        UniqueConstraint("season_id"),
        UniqueConstraint("fm_import_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    season_id: Mapped[int | None] = mapped_column(ForeignKey("season.id", ondelete="CASCADE"))
    fm_import_id: Mapped[int | None]  # FK to fm_import once that table exists (Phase 6)


class PlayerScope(Base):
    """One player's season (or FM snapshot): minutes, role and rating."""

    __tablename__ = "player_scope"
    __table_args__ = (
        CheckConstraint(
            f"primary_role IS NULL OR {_in('primary_role', ROLES)}", name="primary_role"
        ),
        CheckConstraint(f"peer_role IS NULL OR {_in('peer_role', PEER_ROLES)}", name="peer_role"),
        CheckConstraint(
            f"role_source IS NULL OR {_in('role_source', ROLE_SOURCES)}", name="role_source"
        ),
        CheckConstraint(
            f"peer_method IS NULL OR {_in('peer_method', PEER_METHODS)}", name="peer_method"
        ),
        Index(None, "scope_id", "peer_role"),
    )

    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.id", ondelete="CASCADE"), primary_key=True
    )
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope.id", ondelete="CASCADE"), primary_key=True
    )
    minutes: Mapped[int]  # all appearances, canonical minutes (D-010)
    matches: Mapped[int]
    starts: Mapped[int]
    primary_role: Mapped[str | None] = mapped_column(String(4))
    peer_role: Mapped[str | None] = mapped_column(String(4))
    # 'starts': most minutes as a starter. 'listed': no starts, from the profile's listed
    # slot where every formation agrees. NULL: neither gave a role.
    role_source: Mapped[str | None] = mapped_column(String(8))
    avg_rating: Mapped[float | None]  # mean over rated matches only
    rated_matches: Mapped[int]
    computed_at: Mapped[datetime]  # latest normalized_at of the scope's matches: deterministic
    # Central midfielders only (peer role DM or CM; NULL for everyone else), D-018: how the
    # 6/8 group was decided, and the season depths behind it. Depths are shares of the pitch
    # length, like appearance.depth (x 105 for metres).
    peer_method: Mapped[str | None] = mapped_column(String(10))  # depth, partner, formation
    depth: Mapped[float | None]  # in front of his centre-backs, minutes-weighted
    depth_starts: Mapped[int | None]  # central-mid starts with a heatmap behind `depth`
    partner_gap: Mapped[float | None]  # + = his deepest fellow central mid played deeper
    partner_starts: Mapped[int | None]  # of those starts, how many beside another central mid


class PlayerScopeTeam(Base):
    """Minutes per team inside one scope (a mid-season move within a competition)."""

    __tablename__ = "player_scope_team"

    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.id", ondelete="CASCADE"), primary_key=True
    )
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), primary_key=True)
    minutes: Mapped[int]
    matches: Mapped[int]


class PlayerScopeRole(Base):
    """Minutes as a starter in each detailed role: shows how primary_role was chosen."""

    __tablename__ = "player_scope_role"
    __table_args__ = (CheckConstraint(_in("role", ROLES), name="role"),)

    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.id", ondelete="CASCADE"), primary_key=True
    )
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(4), primary_key=True)
    minutes: Mapped[int]
    starts: Mapped[int]


class PlayerScopeStat(Base):
    """One metric over one scope. `minutes` is this metric's exposure: the minutes of the
    appearances that counted for it, not the player's total (SCHEMA §6)."""

    __tablename__ = "player_scope_stat"
    __table_args__ = (
        CheckConstraint("minutes >= 0", name="minutes"),
        Index(None, "scope_id", "metric_id"),
        {"sqlite_with_rowid": False},
    )

    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.id", ondelete="CASCADE"), primary_key=True
    )
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope.id", ondelete="CASCADE"), primary_key=True
    )
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), primary_key=True)
    total: Mapped[float | None]  # successes for fractions; NULL = nothing known
    denominator: Mapped[float | None]  # attempts (or weight), fractions only
    minutes: Mapped[int]
    matches_with_value: Mapped[int]
    per90: Mapped[float | None]  # counts, model outputs, distances; NULL when minutes = 0
    rate: Mapped[float | None]  # fractions: total / denominator; NULL when denominator = 0


# --- peer groups and percentiles (slice 2b; D-014) --------------------------------------------


class PeerGroup(Base):
    """Players of one peer role in one scope with at least `min_minutes`. Upserted on rebuild
    (not deleted), so its ID stays stable and `report checksum` doesn't change."""

    __tablename__ = "peer_group"
    __table_args__ = (
        CheckConstraint(_in("peer_role", PEER_ROLES), name="peer_role"),
        UniqueConstraint("scope_id", "peer_role", "min_minutes"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_id: Mapped[int] = mapped_column(ForeignKey("scope.id", ondelete="CASCADE"))
    peer_role: Mapped[str] = mapped_column(String(4))
    min_minutes: Mapped[int]
    size: Mapped[int]  # players in the group; a metric can rank fewer (see ranked_among)
    computed_at: Mapped[datetime]


class Percentile(Base):
    """One player's rank on one metric within a peer group. Stored raw: a low value is a low
    percentile even for lower-is-better metrics (the UI flips those). A player who isn't
    eligible for a metric (too few minutes on it, too few attempts) has no row."""

    __tablename__ = "percentile"
    __table_args__ = (
        CheckConstraint(_in("basis", PERCENTILE_BASES), name="basis"),
        CheckConstraint("pct >= 0 AND pct <= 100", name="pct"),
        Index(None, "player_id"),
        {"sqlite_with_rowid": False},
    )

    peer_group_id: Mapped[int] = mapped_column(
        ForeignKey("peer_group.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.id", ondelete="CASCADE"), primary_key=True
    )
    metric_id: Mapped[int] = mapped_column(ForeignKey("metric.id"), primary_key=True)
    basis: Mapped[str] = mapped_column(String(8))  # per90 or rate
    value: Mapped[float]  # the per-90 or rate that was ranked
    pct: Mapped[float]  # midpoint percentile, 0-100
    ranked_among: Mapped[int]  # eligible players for this metric in the group
