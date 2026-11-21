"""Slice 2e: what each metric looks like in each peer group, to pick template metrics from.

    uv run python scripts/template_options.py --league l_4WFCIZ --season 2025/2026

Writes one row per metric and peer group to data/template_options.csv (git-ignored, like
everything in data/): players ranked out of the group, share of zeros among them, median
and 90th-percentile value (per 90, or the rate for success rates). Prints a short summary.
Reads scouting.db only (DATABASE_URL); makes no API calls and changes nothing in it.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from scouting.config.settings import Settings
from scouting.db.base import make_engine
from scouting.db.models import Scope
from scouting.db.queries import metric_coverage
from scouting.db.schema_version import SchemaOutOfDateError, check_schema_current
from scouting.ingest.match_list import SyncError
from scouting.ingest.season import find_season

COLUMNS = (
    "metric", "label", "kind", "source", "model", "direction", "applies_to", "basis",
    "min_attempts", "group", "group_size", "ranked", "ranked_share", "zero_share",
    "median", "p90",
)  # fmt: skip


def _num(value: float | None) -> str:
    return "" if value is None else f"{value:.4g}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--league", required=True, help="PitchAPI league ID, e.g. l_4WFCIZ")
    parser.add_argument("--season", required=True, help='season label, e.g. "2025/2026"')
    parser.add_argument("--out", type=Path, default=Path("data/template_options.csv"))
    args = parser.parse_args(argv)

    engine = make_engine(Settings().database_url)
    try:
        check_schema_current(engine)
        with Session(engine) as session:
            season = find_season(session, args.league, args.season)
            scope_id = session.scalar(select(Scope.id).where(Scope.season_id == season.id))
            if scope_id is None:
                raise SyncError("season isn't aggregated yet: run `scouting aggregate`")
            groups, rows = metric_coverage(session, scope_id)
    except (SyncError, SchemaOutOfDateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    if not groups:
        print("error: no peer groups yet: run `scouting aggregate`", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in rows:
            for group in groups:
                cell = row.cells[group.peer_role]
                writer.writerow(
                    (
                        row.code, row.label, row.kind, row.source, int(row.is_model_output),
                        row.direction, row.applies_to, row.basis, row.min_attempts or "",
                        group.peer_role, group.size, cell.ranked,
                        _num(cell.ranked / group.size if group.size else None),
                        _num(cell.zero_share), _num(cell.median), _num(cell.p90),
                    )
                )  # fmt: skip
    print(
        f"{len(rows)} metrics x {len(groups)} peer groups (≥ {groups[0].min_minutes} min: "
        + " · ".join(f"{g.peer_role} {g.size}" for g in groups)
        + f") → {args.out}"
    )
    unranked = [r.code for r in rows if not any(c.ranked for c in r.cells.values())]
    if unranked:
        print(f"ranked in no group: {', '.join(unranked)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
