"""Read-only queries over scouting.db."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from scouting.db.models import (
    Appearance,
    Competition,
    Match,
    MatchSourceId,
    Metric,
    Player,
    PlayerSourceId,
    Season,
    StatValue,
    Team,
)

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
        metrics=metrics,
    )
