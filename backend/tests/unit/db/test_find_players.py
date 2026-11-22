"""find_players: an exact name beats a fragment (slice 2e close-out: "rodri")."""

from __future__ import annotations

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from scouting.db.models import Player, PlayerSourceId, Scope, Season
from scouting.db.queries import find_players
from scouting.domain.names import search_name
from scouting.metrics.aggregate import aggregate_season


def rename(session: Session, external_id: str, name: str, short: str | None = None) -> None:
    player_id = session.scalars(
        select(PlayerSourceId.player_id).where(PlayerSourceId.external_id == external_id)
    ).one()
    session.execute(
        update(Player)
        .where(Player.id == player_id)
        .values(name=name, search_name=search_name(name), short_name=short)
    )


def test_exact_name_wins(synced_season: Engine) -> None:
    with Session(synced_season) as session, session.begin():
        aggregate_season(session, session.scalars(select(Season.id)).one())
        rename(session, "p_AST01", "Rodri")
        rename(session, "p_CST01", "Rodrigo Bentancur")
        rename(session, "p_BAM01", "Guido Rodríguez", short="G. Rodriguez")
    with Session(synced_season) as session:
        scope_id = session.scalars(select(Scope.id)).one()
        assert [p.name for p in find_players(session, scope_id, "rodri")] == ["Rodri"]
        assert [p.name for p in find_players(session, scope_id, "RODRÍ")] == ["Rodri"]
        assert len(find_players(session, scope_id, "rod")) == 3  # no exact match: all of them
        by_short = find_players(session, scope_id, "g. rodriguez")  # a short name counts too
        assert [p.name for p in by_short] == ["Guido Rodríguez"]
