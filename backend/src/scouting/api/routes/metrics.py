"""The metric registry: what every metric means and how it's ranked."""

from __future__ import annotations

from fastapi import APIRouter

from scouting.api.errors import ERROR_RESPONSES, not_found
from scouting.api.schemas import MetricOut, MetricsOut
from scouting.metrics.registry import BY_CODE, REGISTRY, MetricDef

router = APIRouter(tags=["metrics"], responses=ERROR_RESPONSES)


def metric_out(m: MetricDef) -> MetricOut:
    return MetricOut(
        code=m.code,
        label=m.label,
        unit=m.unit,
        kind=m.kind,
        source=m.source,
        basis="rate" if m.is_fraction else "per90",
        model=m.is_model_output,
        direction=m.direction,
        outcome=m.outcome,
        volume_of=m.volume_of,
        applies_to=m.applies_to,
        min_attempts=m.min_attempts,
        description=m.description,
    )


@router.get("/metrics")
def list_metrics() -> MetricsOut:
    """All metrics, in registry order (ingested first, then derived volume metrics)."""
    return MetricsOut(metrics=[metric_out(m) for m in REGISTRY])


@router.get("/metrics/{code}")
def get_metric(code: str) -> MetricOut:
    metric = BY_CODE.get(code)
    if metric is None:
        raise not_found("UNKNOWN_METRIC", f"no metric {code!r}; see /api/v1/metrics")
    return metric_out(metric)
