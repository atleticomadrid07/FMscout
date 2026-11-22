"""Read-only queries over scouting.db."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.orm import Session, aliased

from scouting.db.models import (
    Appearance,
    Competition,
    CompetitionSourceId,
    Match,
    MatchSourceId,
    Metric,
    PeerGroup,
    Percentile,
    Player,
    PlayerScope,
    PlayerScopeRole,
    PlayerScopeStat,
    PlayerScopeTeam,
    PlayerSourceId,
    Scope,
    Season,
    StatValue,
    Team,
)
from scouting.domain.names import search_name
from scouting.domain.positions import PEER_ROLES

SOURCE = "pitchapi"


@dataclass(frozen=True, slots=True)
class MetricLine:
    code: str
    label: str
    kind: str
    source: str
    source_key: str
    value: float | None
    denominator: float | None


@dataclass(frozen=True, slots=True)
class AppearanceDetail:
    player_name: str
    match_label: str
    match_date: date
    competition: str
    season: str
    team: str
    is_starter: bool | None
    formation: str | None
    slot_code: int | None
    sub_category: int | None
    role: str | None
    side: str | None
    is_goalkeeper: bool
    minutes: int
    minutes_actual: int | None
    rating: float | None
    depth: float | None  # slice 2d: 0 = own goal line .. 1 = opponent's
    depth_actions: int | None
    heatmaps_status: str
    metrics: list[MetricLine]


def appearance_detail(
    session: Session, match_external_id: str, player_external_id: str
) -> AppearanceDetail | None:
    """Everything stored for one player in one match, or None if there's no appearance."""
    home, away = aliased(Team), aliased(Team)
    row = session.execute(
        select(Appearance, Player, Match, Team, home, away, Season, Competition)
        .join(Player, Player.id == Appearance.player_id)
        .join(PlayerSourceId, PlayerSourceId.player_id == Player.id)
        .join(Match, Match.id == Appearance.match_id)
        .join(MatchSourceId, MatchSourceId.match_id == Match.id)
        .join(Team, Team.id == Appearance.team_id)
        .join(home, home.id == Match.home_team_id)
        .join(away, away.id == Match.away_team_id)
        .join(Season, Season.id == Match.season_id)
        .join(Competition, Competition.id == Season.competition_id)
        .where(
            PlayerSourceId.source == SOURCE,
            PlayerSourceId.external_id == player_external_id,
            MatchSourceId.source == SOURCE,
            MatchSourceId.external_id == match_external_id,
        )
    ).one_or_none()
    if row is None:
        return None
    appearance, player, match, team, home_team, away_team, season, competition = row

    metrics = [
        MetricLine(
            code=metric.code,
            label=metric.label,
            kind=metric.kind,
            source=metric.source,
            source_key=metric.source_key,
            value=value.value,
            denominator=value.denominator,
        )
        for value, metric in session.execute(
            select(StatValue, Metric)
            .join(Metric, Metric.id == StatValue.metric_id)
            .where(StatValue.appearance_id == appearance.id)
            .order_by(Metric.id)
        )
    ]
    return AppearanceDetail(
        player_name=player.name,
        match_label=f"{home_team.name} v {away_team.name}",
        match_date=match.match_date,
        competition=competition.name,
        season=season.label,
        team=team.name,
        is_starter=appearance.is_starter,
        formation=appearance.formation,
        slot_code=appearance.slot_code,
        sub_category=appearance.sub_category,
        role=appearance.role,
        side=appearance.side,
        is_goalkeeper=appearance.is_goalkeeper,
        minutes=appearance.minutes,
        minutes_actual=appearance.minutes_actual,
        rating=appearance.rating,
        depth=appearance.depth,
        depth_actions=appearance.depth_actions,
        heatmaps_status=match.heatmaps_status,
        metrics=metrics,
    )


# --- season aggregates (slice 2a) -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlayerMatch:
    """A player found by name or ID, with their minutes in the scope."""

    player_id: int
    external_id: str
    name: str
    minutes: int


def find_players(session: Session, scope_id: int, query: str) -> list[PlayerMatch]:
    """Players in a scope matching a PitchAPI ID exactly, or a name fragment (accents and
    case ignored), most minutes first. A player whose whole name or short name is the query
    wins outright ("rodri" finds Rodri, not Bentancur too). Real search (FTS + fuzzy) comes
    in Phase 3."""
    stmt = (
        select(
            Player.id,
            PlayerSourceId.external_id,
            Player.name,
            PlayerScope.minutes,
            Player.search_name,
            Player.short_name,
        )
        .join(PlayerSourceId, PlayerSourceId.player_id == Player.id)
        .join(PlayerScope, PlayerScope.player_id == Player.id)
        .where(PlayerSourceId.source == SOURCE, PlayerScope.scope_id == scope_id)
        .order_by(PlayerScope.minutes.desc(), Player.id)
    )
    if query.startswith("p_"):
        stmt = stmt.where(PlayerSourceId.external_id == query)
    else:
        fragment = search_name(query)
        stmt = stmt.where(
            Player.search_name.contains(fragment, autoescape=True)
            | func.lower(Player.short_name).contains(fragment, autoescape=True)
        )
    rows = list(session.execute(stmt))
    if not query.startswith("p_"):
        wanted = search_name(query)
        exact = [
            r for r in rows if wanted in (r[4], search_name(r[5]) if r[5] is not None else None)
        ]
        rows = exact or rows
    return [PlayerMatch(r[0], r[1], r[2], r[3]) for r in rows]


@dataclass(frozen=True, slots=True)
class SeasonStatLine:
    code: str
    label: str
    kind: str
    unit: str
    value_is_rate: bool
    is_model_output: bool
    total: float | None
    denominator: float | None
    minutes: int
    matches: int
    per90: float | None
    rate: float | None
    direction: str = "higher"
    min_attempts: int | None = None
    pct: float | None = None  # within the player's peer group; None = not ranked
    ranked_among: int | None = None


@dataclass(frozen=True, slots=True)
class GroupInfo:
    peer_role: str
    size: int
    min_minutes: int


@dataclass(frozen=True, slots=True)
class PlayerSeason:
    name: str
    external_id: str
    competition: str
    season: str
    minutes: int
    matches: int
    starts: int
    primary_role: str | None
    peer_role: str | None
    role_source: str | None
    avg_rating: float | None
    rated_matches: int
    teams: list[tuple[str, int, int]]  # (team, minutes, matches)
    roles: list[tuple[str, int, int]]  # (role, minutes as a starter, starts)
    stats: list[SeasonStatLine]
    group: GroupInfo | None  # the peer group he's ranked in; None = not in one
    group_min_minutes: int | None  # the scope's threshold, to explain why he isn't
    # Central mids only (D-018): how the 6/8 group was decided and the depths behind it.
    peer_method: str | None = None
    depth: float | None = None  # share of the pitch length in front of his centre-backs
    depth_starts: int | None = None
    partner_gap: float | None = None
    partner_starts: int | None = None


def player_season(session: Session, scope_id: int, player_id: int) -> PlayerSeason | None:
    """Everything aggregated for one player in one scope, or None if they have no row."""
    row = session.execute(
        select(PlayerScope, Player, PlayerSourceId.external_id, Season, Competition)
        .join(Player, Player.id == PlayerScope.player_id)
        .join(PlayerSourceId, PlayerSourceId.player_id == Player.id)
        .join(Scope, Scope.id == PlayerScope.scope_id)
        .join(Season, Season.id == Scope.season_id)
        .join(Competition, Competition.id == Season.competition_id)
        .where(
            PlayerScope.scope_id == scope_id,
            PlayerScope.player_id == player_id,
            PlayerSourceId.source == SOURCE,
        )
    ).one_or_none()
    if row is None:
        return None
    scope_row, player, external_id, season, competition = row
    teams = [
        (name, minutes, matches)
        for name, minutes, matches in session.execute(
            select(Team.name, PlayerScopeTeam.minutes, PlayerScopeTeam.matches)
            .join(Team, Team.id == PlayerScopeTeam.team_id)
            .where(PlayerScopeTeam.scope_id == scope_id, PlayerScopeTeam.player_id == player_id)
            .order_by(PlayerScopeTeam.minutes.desc(), Team.name)
        )
    ]
    roles = [
        (role, minutes, starts)
        for role, minutes, starts in session.execute(
            select(PlayerScopeRole.role, PlayerScopeRole.minutes, PlayerScopeRole.starts)
            .where(PlayerScopeRole.scope_id == scope_id, PlayerScopeRole.player_id == player_id)
            .order_by(PlayerScopeRole.minutes.desc(), PlayerScopeRole.role)
        )
    ]
    group_row = (
        session.scalars(
            select(PeerGroup).where(
                PeerGroup.scope_id == scope_id, PeerGroup.peer_role == scope_row.peer_role
            )
        ).first()
        if scope_row.peer_role is not None
        else None
    )
    any_group = session.scalars(select(PeerGroup).where(PeerGroup.scope_id == scope_id)).first()
    in_group = group_row is not None and scope_row.minutes >= group_row.min_minutes
    ranks: dict[int, tuple[float, int]] = {}
    if in_group and group_row is not None:
        ranks = {
            metric_id: (pct, n)
            for metric_id, pct, n in session.execute(
                select(Percentile.metric_id, Percentile.pct, Percentile.ranked_among).where(
                    Percentile.peer_group_id == group_row.id, Percentile.player_id == player_id
                )
            )
        }
    stats = [
        SeasonStatLine(
            code=metric.code,
            label=metric.label,
            kind=metric.kind,
            unit=metric.unit,
            value_is_rate=metric.value_is_rate,
            is_model_output=metric.is_model_output,
            total=stat.total,
            denominator=stat.denominator,
            minutes=stat.minutes,
            matches=stat.matches_with_value,
            per90=stat.per90,
            rate=stat.rate,
            direction=metric.direction,
            min_attempts=metric.min_attempts,
            pct=ranks[metric.id][0] if metric.id in ranks else None,
            ranked_among=ranks[metric.id][1] if metric.id in ranks else None,
        )
        for stat, metric in session.execute(
            select(PlayerScopeStat, Metric)
            .join(Metric, Metric.id == PlayerScopeStat.metric_id)
            .where(PlayerScopeStat.scope_id == scope_id, PlayerScopeStat.player_id == player_id)
            .order_by(Metric.id)
        )
    ]
    return PlayerSeason(
        name=player.name,
        external_id=external_id,
        competition=competition.name,
        season=season.label,
        minutes=scope_row.minutes,
        matches=scope_row.matches,
        starts=scope_row.starts,
        primary_role=scope_row.primary_role,
        peer_role=scope_row.peer_role,
        role_source=scope_row.role_source,
        avg_rating=scope_row.avg_rating,
        rated_matches=scope_row.rated_matches,
        teams=teams,
        roles=roles,
        stats=stats,
        group=(
            GroupInfo(group_row.peer_role, group_row.size, group_row.min_minutes)
            if in_group and group_row is not None
            else None
        ),
        group_min_minutes=any_group.min_minutes if any_group is not None else None,
        peer_method=scope_row.peer_method,
        depth=scope_row.depth,
        depth_starts=scope_row.depth_starts,
        partner_gap=scope_row.partner_gap,
        partner_starts=scope_row.partner_starts,
    )


@dataclass(frozen=True, slots=True)
class MatchValue:
    match_date: date
    match_label: str
    minutes: int
    value: float | None
    denominator: float | None


def metric_by_match(
    session: Session, season_id: int, player_id: int, metric_code: str
) -> list[MatchValue]:
    """One metric's stored per-match values for a player over a season, for hand checks.
    Matches without a row (metric doesn't apply, or no data) are left out."""
    home, away = aliased(Team), aliased(Team)
    return [
        MatchValue(match.match_date, f"{h} v {a}", minutes, value, denominator)
        for match, h, a, minutes, value, denominator in session.execute(
            select(
                Match,
                home.name,
                away.name,
                Appearance.minutes,
                StatValue.value,
                StatValue.denominator,
            )
            .join(Appearance, Appearance.match_id == Match.id)
            .join(StatValue, StatValue.appearance_id == Appearance.id)
            .join(Metric, Metric.id == StatValue.metric_id)
            .join(home, home.id == Match.home_team_id)
            .join(away, away.id == Match.away_team_id)
            .where(
                Match.season_id == season_id,
                Appearance.player_id == player_id,
                Metric.code == metric_code,
            )
            .order_by(Match.kickoff_utc, Match.id)
        )
    ]


# --- peer reports (slice 2b) --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PeerRow:
    name: str
    external_id: str
    team: str
    minutes: int
    value: float
    pct: float


@dataclass(frozen=True, slots=True)
class PeerRanking:
    group: GroupInfo
    metric_label: str
    basis: str
    direction: str
    is_model_output: bool
    ranked_among: int
    rows: list[PeerRow]  # best value first


def _main_teams(session: Session, scope_id: int) -> dict[int, str]:
    """Each player's team with the most minutes in the scope."""
    best: dict[int, tuple[int, str]] = {}
    for player_id, team, minutes in session.execute(
        select(PlayerScopeTeam.player_id, Team.name, PlayerScopeTeam.minutes)
        .join(Team, Team.id == PlayerScopeTeam.team_id)
        .where(PlayerScopeTeam.scope_id == scope_id)
        .order_by(PlayerScopeTeam.player_id, Team.name)
    ):
        if player_id not in best or minutes > best[player_id][0]:
            best[player_id] = (minutes, team)
    return {pid: team for pid, (_, team) in best.items()}


def peer_ranking(
    session: Session, scope_id: int, peer_role: str, metric_code: str
) -> PeerRanking | None:
    """Everyone ranked on one metric in one peer group, highest value first. None if the
    scope has no such group."""
    group = session.scalars(
        select(PeerGroup).where(PeerGroup.scope_id == scope_id, PeerGroup.peer_role == peer_role)
    ).first()
    if group is None:
        return None
    metric = session.scalars(select(Metric).where(Metric.code == metric_code)).one()
    teams = _main_teams(session, scope_id)
    rows = [
        PeerRow(name, external_id, teams.get(player_id, "?"), minutes, value, pct)
        for player_id, name, external_id, minutes, value, pct in session.execute(
            select(
                Player.id,
                Player.name,
                PlayerSourceId.external_id,
                PlayerScope.minutes,
                Percentile.value,
                Percentile.pct,
            )
            .join(Player, Player.id == Percentile.player_id)
            .join(PlayerSourceId, PlayerSourceId.player_id == Player.id)
            .join(
                PlayerScope,
                (PlayerScope.player_id == Player.id) & (PlayerScope.scope_id == scope_id),
            )
            .where(
                Percentile.peer_group_id == group.id,
                Percentile.metric_id == metric.id,
                PlayerSourceId.source == SOURCE,
            )
            .order_by(Percentile.value.desc(), Player.name)
        )
    ]
    return PeerRanking(
        group=GroupInfo(group.peer_role, group.size, group.min_minutes),
        metric_label=metric.label,
        basis="rate" if metric.aggregation == "rate_from_sums" else "per90",
        direction=metric.direction,
        is_model_output=metric.is_model_output,
        ranked_among=len(rows),
        rows=rows,
    )


@dataclass(frozen=True, slots=True)
class ThresholdRow:
    code: str
    min_attempts: int
    # peer role -> (ranked, members with data for the metric)
    counts: dict[str, tuple[int, int]]


def threshold_report(session: Session, scope_id: int) -> tuple[list[GroupInfo], list[ThresholdRow]]:
    """For every metric with a minimum number of attempts: per peer group, how many players
    have data for it and how many of those are ranked (met the minimum)."""
    groups = sorted(
        session.scalars(select(PeerGroup).where(PeerGroup.scope_id == scope_id)),
        key=lambda g: PEER_ROLES.index(g.peer_role),
    )
    infos = [GroupInfo(g.peer_role, g.size, g.min_minutes) for g in groups]
    report: list[ThresholdRow] = []
    for metric in session.scalars(
        select(Metric).where(Metric.min_attempts.is_not(None)).order_by(Metric.id)
    ):
        assert metric.min_attempts is not None  # filtered above; narrows the type
        counts: dict[str, tuple[int, int]] = {}
        for group in groups:
            with_data = session.scalar(
                select(func.count())
                .select_from(PlayerScopeStat)
                .join(
                    PlayerScope,
                    (PlayerScope.player_id == PlayerScopeStat.player_id)
                    & (PlayerScope.scope_id == PlayerScopeStat.scope_id),
                )
                .where(
                    PlayerScopeStat.scope_id == scope_id,
                    PlayerScopeStat.metric_id == metric.id,
                    PlayerScopeStat.rate.is_not(None),
                    PlayerScope.peer_role == group.peer_role,
                    PlayerScope.minutes >= group.min_minutes,
                )
            )
            ranked = session.scalar(
                select(func.count())
                .select_from(Percentile)
                .where(Percentile.peer_group_id == group.id, Percentile.metric_id == metric.id)
            )
            counts[group.peer_role] = (ranked or 0, with_data or 0)
        report.append(ThresholdRow(metric.code, metric.min_attempts, counts))
    return infos, report


# --- metric coverage per peer group (slice 2e: template options) -----------------------------


@dataclass(frozen=True, slots=True)
class CoverageCell:
    """One metric in one peer group: how many are ranked, and the shape of their values."""

    ranked: int  # players with a percentile for the metric (eligible)
    zero_share: float | None  # share of the ranked whose value is 0 (rare events); None: nobody
    median: float | None  # of the ranked players' values (per 90 or rate, as ranked)
    p90: float | None  # the value at the group's 90th percentile


@dataclass(frozen=True, slots=True)
class CoverageRow:
    code: str
    label: str
    kind: str
    source: str
    is_model_output: bool
    direction: str
    applies_to: str
    basis: str  # per90 or rate: what the percentiles rank
    min_attempts: int | None
    cells: dict[str, CoverageCell]  # peer role -> cell


def _quantile(ordered: list[float], q: float) -> float:
    """Linear interpolation between the closest ranks (the 'inclusive' method)."""
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def metric_coverage(session: Session, scope_id: int) -> tuple[list[GroupInfo], list[CoverageRow]]:
    """For every metric and peer group: players ranked, the share of zeros, the median and
    the 90th-percentile value, from the stored percentiles (the values they rank). Shows
    which metrics carry a signal for a position before they go on a template. Read-only."""
    groups = sorted(
        session.scalars(select(PeerGroup).where(PeerGroup.scope_id == scope_id)),
        key=lambda g: PEER_ROLES.index(g.peer_role),
    )
    values: dict[tuple[int, int], list[float]] = {}
    for group_id, metric_id, value in session.execute(
        select(Percentile.peer_group_id, Percentile.metric_id, Percentile.value).where(
            Percentile.peer_group_id.in_([g.id for g in groups])
        )
    ):
        values.setdefault((group_id, metric_id), []).append(value)
    rows: list[CoverageRow] = []
    for metric in session.scalars(select(Metric).order_by(Metric.id)):
        cells: dict[str, CoverageCell] = {}
        for group in groups:
            ranked = sorted(values.get((group.id, metric.id), []))
            cells[group.peer_role] = CoverageCell(
                ranked=len(ranked),
                zero_share=sum(v == 0 for v in ranked) / len(ranked) if ranked else None,
                median=_quantile(ranked, 0.5) if ranked else None,
                p90=_quantile(ranked, 0.9) if ranked else None,
            )
        rows.append(
            CoverageRow(
                code=metric.code,
                label=metric.label,
                kind=metric.kind,
                source=metric.source,
                is_model_output=metric.is_model_output,
                direction=metric.direction,
                applies_to=metric.applies_to,
                basis="rate" if metric.aggregation == "rate_from_sums" else "per90",
                min_attempts=metric.min_attempts,
                cells=cells,
            )
        )
    return [GroupInfo(g.peer_role, g.size, g.min_minutes) for g in groups], rows


# --- scopes for the API (slice 3a) -----------------------------------------------------------


def scope_key(league_external_id: str, season_label: str) -> str:
    """A season's public key, built from source IDs so it survives a database rebuilt from
    scratch: "l_4WFCIZ.2025-2026" (the slash would break URL paths)."""
    return f"{league_external_id}.{season_label.replace('/', '-')}"


@dataclass(frozen=True, slots=True)
class ScopeCoverage:
    """What the season's matches have, so the UI can show gaps instead of blanks."""

    matches: int
    players_missing: int  # no /players data (the match adds no stats)
    advanced_unavailable: int  # ANALYTICS_UNAVAILABLE: no advanced metrics for the match
    lineups_missing: int  # no roles or starts for the match
    heatmaps_missing: int  # no depth (the 6/8 split falls back to formations)
    pending: int  # not synced yet in at least one part
    matches_with_ratings: int  # 0 for a league without ratings


@dataclass(frozen=True, slots=True)
class ScopeSummary:
    key: str
    season_id: int
    scope_id: int | None  # None: never aggregated
    competition: str
    country_code: str
    season: str
    is_current: bool
    coverage: ScopeCoverage
    min_minutes: int | None  # the stored percentiles' threshold; None before aggregation
    computed_at: datetime | None  # when its aggregates' data was last normalized
    groups: list[GroupInfo]  # peer groups at min_minutes, in PEER_ROLES order


def scope_summaries(session: Session) -> list[ScopeSummary]:
    """Every season in scouting.db with its coverage and peer groups, newest season first."""
    seasons = session.execute(
        select(Season, Competition, CompetitionSourceId.external_id)
        .join(Competition, Competition.id == Season.competition_id)
        .join(CompetitionSourceId, CompetitionSourceId.competition_id == Competition.id)
        .where(CompetitionSourceId.source == SOURCE)
        .order_by(Season.label.desc(), Competition.name)
    ).all()
    summaries = []
    for season, competition, league_id in seasons:
        min_minutes, computed_at, groups = _groups(session, season.id)
        summaries.append(
            ScopeSummary(
                key=scope_key(league_id, season.label),
                season_id=season.id,
                scope_id=session.scalar(select(Scope.id).where(Scope.season_id == season.id)),
                competition=competition.name,
                country_code=competition.country_code,
                season=season.label,
                is_current=season.is_current,
                coverage=_coverage(session, season.id),
                min_minutes=min_minutes,
                computed_at=computed_at,
                groups=groups,
            )
        )
    return summaries


def find_scope(session: Session, key: str) -> ScopeSummary | None:
    return next((s for s in scope_summaries(session) if s.key == key), None)


def _count(condition: ColumnElement[bool]) -> ColumnElement[int]:
    return func.sum(case((condition, 1), else_=0))


def _coverage(session: Session, season_id: int) -> ScopeCoverage:
    row = session.execute(
        select(
            func.count(Match.id),
            _count(Match.players_status == "missing"),
            _count(Match.advanced_status == "unavailable"),
            _count(Match.lineups_status == "missing"),
            _count(Match.heatmaps_status == "missing"),
            _count(
                (Match.players_status == "pending")
                | (Match.advanced_status == "pending")
                | (Match.lineups_status == "pending")
                | (Match.heatmaps_status == "pending")
            ),
        ).where(Match.season_id == season_id)
    ).one()
    rated = session.scalar(
        select(func.count(func.distinct(Appearance.match_id)))
        .join(Match, Match.id == Appearance.match_id)
        .where(Match.season_id == season_id, Appearance.rating.is_not(None))
    )
    matches, players, advanced, lineups, heatmaps, pending = (int(v or 0) for v in row)
    return ScopeCoverage(
        matches=matches,
        players_missing=players,
        advanced_unavailable=advanced,
        lineups_missing=lineups,
        heatmaps_missing=heatmaps,
        pending=pending,
        matches_with_ratings=int(rated or 0),
    )


def _groups(
    session: Session, season_id: int
) -> tuple[int | None, datetime | None, list[GroupInfo]]:
    groups = session.scalars(
        select(PeerGroup)
        .join(Scope, Scope.id == PeerGroup.scope_id)
        .where(Scope.season_id == season_id)
    ).all()
    if not groups:
        return None, None, []
    ordered = sorted(groups, key=lambda g: PEER_ROLES.index(g.peer_role))
    return (
        ordered[0].min_minutes,
        max(g.computed_at for g in groups),
        [GroupInfo(g.peer_role, g.size, g.min_minutes) for g in ordered],
    )


# --- players for the API (slice 3b) ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchEntry:
    """One player of a scope, as the search index needs him."""

    player_id: int
    external_id: str
    name: str
    short_name: str | None
    team: str | None  # the team he played most minutes for
    peer_role: str | None
    minutes: int


def search_entries(session: Session, scope_id: int) -> list[SearchEntry]:
    teams = _main_teams(session, scope_id)
    return [
        SearchEntry(player_id, external_id, name, short_name, teams.get(player_id), role, minutes)
        for player_id, external_id, name, short_name, role, minutes in session.execute(
            select(
                Player.id,
                PlayerSourceId.external_id,
                Player.name,
                Player.short_name,
                PlayerScope.peer_role,
                PlayerScope.minutes,
            )
            .join(PlayerSourceId, PlayerSourceId.player_id == Player.id)
            .join(PlayerScope, PlayerScope.player_id == Player.id)
            .where(PlayerSourceId.source == SOURCE, PlayerScope.scope_id == scope_id)
            .order_by(Player.id)
        )
    ]


def player_id_for(session: Session, external_id: str) -> int | None:
    return session.scalar(
        select(PlayerSourceId.player_id).where(
            PlayerSourceId.source == SOURCE, PlayerSourceId.external_id == external_id
        )
    )


@dataclass(frozen=True, slots=True)
class PlayerExtras:
    """What the player card needs beyond PlayerSeason: the profile and his coverage."""

    short_name: str | None
    country_code: str | None
    image_url: str | None
    matches_without_advanced: int  # his matches with ANALYTICS_UNAVAILABLE
    matches_without_heatmap: int  # his matches whose heatmaps are missing (or not fetched)


def player_extras(session: Session, season_id: int, player_id: int) -> PlayerExtras:
    player = session.get_one(Player, player_id)
    advanced, heatmap = session.execute(
        select(
            _count(Match.advanced_status == "unavailable"),
            _count(Match.heatmaps_status != "ok"),
        )
        .join(Appearance, Appearance.match_id == Match.id)
        .where(Match.season_id == season_id, Appearance.player_id == player_id)
    ).one()
    return PlayerExtras(
        player.short_name, player.country_code, player.image_url, int(advanced or 0),
        int(heatmap or 0),
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class PlayerMatchValue:
    """One appearance and one metric's stored value in it (form and trend lines)."""

    match_id: str  # PitchAPI match ID
    match_date: date
    team: str
    opponent: str
    home: bool
    goals_for: int | None
    goals_against: int | None
    minutes: int
    started: bool | None
    value: float | None  # NULL stays NULL; successes for a success rate
    denominator: float | None  # attempts for a success rate


def player_matches(
    session: Session, season_id: int, player_id: int, metric_code: str
) -> list[PlayerMatchValue]:
    """Every appearance of the player in the season, oldest first, with the metric's stored
    value. Appearances without a row (no data that match) are kept, with value None, so a
    trend line shows the gap instead of skipping the match."""
    metric_id = session.scalar(select(Metric.id).where(Metric.code == metric_code))
    team, opponent = aliased(Team), aliased(Team)
    rows = session.execute(
        select(
            MatchSourceId.external_id.label("match_id"),
            Match.match_date,
            Match.home_team_id,
            Match.home_score,
            Match.away_score,
            team.name.label("team_name"),
            opponent.name.label("opponent_name"),
            Appearance.team_id,
            Appearance.minutes,
            Appearance.is_starter.label("started"),
            StatValue.value,
            StatValue.denominator,
        )
        .join(Appearance, Appearance.match_id == Match.id)
        .join(MatchSourceId, MatchSourceId.match_id == Match.id)
        .join(team, team.id == Appearance.team_id)
        .join(
            opponent,
            opponent.id
            == case(
                (Match.home_team_id == Appearance.team_id, Match.away_team_id),
                else_=Match.home_team_id,
            ),
        )
        .outerjoin(
            StatValue,
            (StatValue.appearance_id == Appearance.id) & (StatValue.metric_id == metric_id),
        )
        .where(
            Match.season_id == season_id,
            Appearance.player_id == player_id,
            MatchSourceId.source == SOURCE,
        )
        .order_by(Match.kickoff_utc, Match.id)
    )
    out = []
    for row in rows:
        home = row.home_team_id == row.team_id
        out.append(
            PlayerMatchValue(
                match_id=row.match_id,
                match_date=row.match_date,
                team=row.team_name,
                opponent=row.opponent_name,
                home=home,
                goals_for=row.home_score if home else row.away_score,
                goals_against=row.away_score if home else row.home_score,
                minutes=row.minutes,
                started=row.started,
                value=row.value,
                denominator=row.denominator,
            )
        )
    return out
