"""Read-only reports over scouting.db (slice 1d)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from scouting.db.base import Base
from scouting.db.models import Appearance, Match
from scouting.domain.positions import FORMATION_ROLES


def checksum(engine: Engine) -> tuple[str, dict[str, int]]:
    """SHA-256 over every canonical table's rows (sorted), plus row counts per table.

    Two databases with the same checksum hold the same data. Used for the "running twice
    changes nothing" and "resume equals rebuild" checks.
    """
    digest = hashlib.sha256()
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            rows = sorted(
                json.dumps(list(row), default=str, separators=(",", ":"))
                for row in conn.execute(select(table))
            )
            counts[table.name] = len(rows)
            digest.update(table.name.encode())
            for row in rows:
                digest.update(row.encode())
    return digest.hexdigest(), counts


@dataclass(slots=True)
class FormationReport:
    matches: int
    formations: Counter[str] = field(default_factory=Counter)  # team-matches per formation
    unmapped_formations: list[str] = field(default_factory=list)
    unmapped_slots: Counter[tuple[str | None, int | None]] = field(default_factory=Counter)
    starters_without_role: int = 0


def formation_report(session: Session, season_id: int) -> FormationReport:
    matches = session.scalars(select(Match).where(Match.season_id == season_id)).all()
    report = FormationReport(matches=len(matches))
    for match in matches:
        for formation in (match.home_formation, match.away_formation):
            if formation is not None:
                report.formations[formation] += 1
    report.unmapped_formations = sorted(f for f in report.formations if f not in FORMATION_ROLES)
    rows = session.execute(
        select(Appearance.formation, Appearance.slot_code, func.count())
        .join(Match, Match.id == Appearance.match_id)
        .where(
            Match.season_id == season_id,
            Appearance.is_starter.is_(True),
            Appearance.role.is_(None),
        )
        .group_by(Appearance.formation, Appearance.slot_code)
    ).all()
    for formation, slot, count in rows:
        report.unmapped_slots[(formation, slot)] = count
        report.starters_without_role += count
    return report
