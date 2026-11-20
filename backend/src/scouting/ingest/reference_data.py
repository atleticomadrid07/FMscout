"""Copy code-defined reference data into scouting.db: the metric registry and the
formation → role table. Code is the source of truth; running this twice changes nothing.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from scouting.db.models import FormationRole, Metric
from scouting.domain.positions import formation_role_rows
from scouting.metrics.registry import REGISTRY, MetricDef


def _metric_values(d: MetricDef) -> dict[str, object]:
    return {
        "label": d.label,
        "unit": d.unit,
        "kind": d.kind,
        "source": d.source,
        "source_key": d.source_key,
        "absence_policy": d.absence_policy,
        "aggregation": d.aggregation,
        "applies_to": d.applies_to,
        "per90_allowed": d.per90_allowed,
        "is_model_output": d.is_model_output,
        "higher_is_better": d.higher_is_better,
        "description": d.description,
    }


def sync_metrics(session: Session) -> dict[str, int]:
    """Upsert every registry metric by code; return code → metric.id.

    Metrics removed from the registry are left in the table (stat rows may reference
    them); a removed code must never be reused for a different metric.
    """
    existing = {m.code: m for m in session.scalars(select(Metric))}
    for definition in REGISTRY:
        values = _metric_values(definition)
        metric = existing.get(definition.code)
        if metric is None:
            metric = Metric(code=definition.code, **values)
            session.add(metric)
            existing[definition.code] = metric
        else:
            for name, value in values.items():
                if getattr(metric, name) != value:
                    setattr(metric, name, value)
    session.flush()
    return {code: metric.id for code, metric in existing.items()}


def sync_formation_roles(session: Session) -> None:
    """Make `formation_role` match scouting.domain.positions exactly."""
    wanted = {(f, s): (role, side) for f, s, role, side in formation_role_rows()}
    for row in session.scalars(select(FormationRole)):
        key = (row.formation, row.slot_code)
        if key not in wanted:
            session.delete(row)
            continue
        role, side = wanted.pop(key)
        if (row.role, row.side) != (role, side):
            row.role, row.side = role, side
    for (formation, slot), (role, side) in wanted.items():
        session.add(FormationRole(formation=formation, slot_code=slot, role=role, side=side))
    session.flush()


def sync_reference_data(session: Session) -> dict[str, int]:
    sync_formation_roles(session)
    return sync_metrics(session)
