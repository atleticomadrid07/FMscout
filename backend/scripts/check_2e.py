"""Slice 2e data checks: what two striker-template metrics contain.

    uv run python scripts/check_2e.py --league l_4WFCIZ --season 2025/2026

1. Does xGOT include penalties? (appearances whose only shot was a scored penalty)
2. What adds up to defensive_actions? (every combination of candidate counts)

Reads scouting.db only (DATABASE_URL); makes no API calls and writes nothing. The checks
live in scouting.db.checks (tested); this file only prints them.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session

from scouting.config.settings import Settings
from scouting.db.base import make_engine
from scouting.db.checks import (
    CheckError,
    DefensiveActionsCheck,
    OneShotGoals,
    XgotPenaltyCheck,
    check_defensive_actions,
    check_xgot_penalties,
)
from scouting.db.schema_version import SchemaOutOfDateError, check_schema_current
from scouting.ingest.match_list import SyncError
from scouting.ingest.season import find_season


def _share(part: int, whole: int) -> str:
    return "n/a" if whole == 0 else f"{part / whole:.1%}"


def _goals_line(label: str, g: OneShotGoals) -> str:
    mean = "n/a" if g.mean_xgot is None else f"{g.mean_xgot:.3f}"
    return (
        f"  {label:<36} {g.rows:>4} appearances · xGOT > 0 in {g.xgot_positive} "
        f"({_share(g.xgot_positive, g.rows)}) · mean xGOT {mean}"
    )


def print_xgot(check: XgotPenaltyCheck) -> None:
    print("1. Does xGOT include penalties?")
    print(_goals_line("only shot = a scored penalty", check.penalty))
    print(_goals_line("only shot = a scored non-penalty", check.open_play))
    print("  (scored shots are on target: xGOT > 0 for penalties means they're included)")


def print_defensive(check: DefensiveActionsCheck) -> None:
    print("2. What adds up to defensive_actions?")
    print(f"  {check.rows} outfield appearances with defensive_actions stored")
    for formula in check.best:
        print(
            f"  {_share(formula.exact, check.rows):>6} exact · mean gap {formula.mean_gap:+.2f} · "
            + " + ".join(formula.components)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--league", required=True, help="PitchAPI league ID, e.g. l_4WFCIZ")
    parser.add_argument("--season", required=True, help='season label, e.g. "2025/2026"')
    parser.add_argument("--only", type=int, choices=(1, 2), help="run one check")
    args = parser.parse_args(argv)

    engine = make_engine(Settings().database_url)
    try:
        check_schema_current(engine)
        with Session(engine) as session:
            season_id = find_season(session, args.league, args.season).id
            if args.only in (None, 1):
                print_xgot(check_xgot_penalties(session, season_id))
            if args.only is None:
                print()
            if args.only in (None, 2):
                print_defensive(check_defensive_actions(session, season_id))
    except (SyncError, SchemaOutOfDateError, CheckError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
