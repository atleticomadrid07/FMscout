"""Slice 2c data checks: the open questions in PLAN.md, answered from scouting.db.

    uv run python scripts/check_2c.py --league l_4WFCIZ --season 2025/2026

1. was_fouled: does NULL mean 0? (per match: team's was_fouled vs opponent's fouls)
2. launch_pct (and claim_rate): is the sent rate just the ratio of the counts?
3. Role judgement calls: who starts in 4-2-3-1's 64/66, 3-4-2-1's 84/86 and slot 95
4. xA vs xAG: per match, and how differently they rank each peer group
5. Is /players' expected_assists real xA (credits passes with no shot), or shot-based?

    uv run python scripts/check_2c.py --league l_4WFCIZ --season 2025/2026 --only 5

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
    GAP_CLAMP,
    RATE_SPECS,
    SLOT_SPECS,
    CheckError,
    FouledCheck,
    RateCheck,
    SlotGroup,
    XaCheck,
    XaDefinitionCheck,
    check_rate,
    check_slots,
    check_was_fouled,
    check_xa_definition,
    check_xa_xag,
)
from scouting.db.schema_version import SchemaOutOfDateError, check_schema_current
from scouting.ingest.match_list import SyncError
from scouting.ingest.season import find_season


def _share(part: int, whole: int) -> str:
    return "n/a" if whole == 0 else f"{part / whole:.1%}"


def _num(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _gap_label(gap: int) -> str:
    if gap <= -GAP_CLAMP:
        return f"<={gap:+d}"
    return f">={gap:+d}" if gap >= GAP_CLAMP else f"{gap:+d}"


def print_fouled(check: FouledCheck) -> None:
    print("1. was_fouled vs the opponent's fouls_committed (per match and team, NULL read as 0)")
    values = ", ".join(f"{'NULL' if v is None else f'{v:g}'}: {n}" for v, n in check.values.items())
    print(f"   stored was_fouled values (outfield rows): {values}")
    print(
        f"   {check.team_matches} team-matches · gap 0: {check.exact} "
        f"({_share(check.exact, check.team_matches)}) · |gap| <= 1: {check.within_one} "
        f"({_share(check.within_one, check.team_matches)})"
    )
    print(
        f"   season: opponent fouls {check.opponent_fouls:g}, known was_fouled "
        f"{check.known_fouled:g}"
        + (
            f" · {check.one_sided} team-matches skipped (no opponent rows)"
            if check.one_sided
            else ""
        )
        + (f" · {check.fouls_null_rows} NULL fouls rows" if check.fouls_null_rows else "")
    )
    gaps = "  ".join(f"{_gap_label(g)}: {n}" for g, n in check.gaps.items())
    print(f"   gap = opponent fouls - team was_fouled: {gaps}")
    print("   by NULL rows in the team (flat mean gap -> NULL means 0; rising -> unknown):")
    print(f"     {'NULL rows':<10} {'team-matches':>12} {'mean gap':>9} {'gap 0':>7}")
    for b in check.buckets:
        print(
            f"     {b.label:<10} {b.team_matches:>12} {_num(b.mean_gap, 2):>9} "
            f"{_share(b.exact, b.team_matches):>7}"
        )
    print("   (keepers have no was_fouled row, so a foul on a keeper is a gap of +1)")


def print_rate(check: RateCheck) -> None:
    scale = "percent (0-100)" if check.scale == 100 else "fraction (0-1)"
    print(f"   {check.rate_code} vs {check.formula} · PitchAPI sends a {scale}")
    print(
        f"     {check.rows_with_rate} keeper rows: {check.compared} compared, "
        f"{check.no_attempts} with 0 attempts, {check.missing} with a NULL or missing value"
    )
    print(
        f"     within ±{check.tolerance:g}: {check.within_tolerance} "
        f"({_share(check.within_tolerance, check.compared)}) · mean diff "
        f"{_num(check.mean_diff, 4)} · max |diff| {_num(check.max_abs_diff, 4)}"
    )
    if check.worst and (check.max_abs_diff or 0) > 0:
        print("     largest differences (sent vs from counts):")
        for row in check.worst:
            print(
                f"       {row.player:<24} {row.match_label:<44} {row.sent:>8.3f} "
                f"{row.from_counts:>8.3f}  (attempts {row.attempts:g})"
            )


def print_slots(group: SlotGroup, top: int) -> None:
    print(f"   {group.label} (mapped {group.mapped_role}): {group.starts} starts")
    if not group.players:
        print("     nobody started there")
        return
    print(
        f"     {'player':<26} {'team':<22} {'starts':>6} {'min':>6}  {'season role (peer)':<18}"
        " starts by slot/formation"
    )
    for p in group.players[:top]:
        role = (
            "not aggregated"
            if p.season_minutes is None
            else (f"{p.primary_role or '-'} ({p.peer_role or '-'})")
        )
        print(
            f"     {p.name:<26} {p.team:<22} {p.starts:>6} {p.minutes:>6}  {role:<18} {p.breakdown}"
        )
    rest = group.players[top:]
    if rest:
        print(
            f"     ... and {len(rest)} more ({sum(p.starts for p in rest)} starts, "
            f"{sum(p.minutes for p in rest)} min)"
        )


def print_xa(check: XaCheck) -> None:
    print("4. xA (/players expected_assists) vs xAG (advanced creation.xag)")
    print(
        f"   per match: {check.with_both} rows with both · equal to 2 decimals: {check.equal} "
        f"({_share(check.equal, check.with_both)}) · r = {_num(check.correlation)} · "
        f"totals xA {check.total_xa:.2f}, xAG {check.total_xag:.2f}"
    )
    if check.xa_only or check.xag_only:
        print(f"   rows with only xA: {check.xa_only} · only xAG: {check.xag_only}")
    print("   per peer group (percentiles on per-90s; gap = xAG pct - xA pct):")
    print(f"     {'group':<12} {'ranked':>6} {'r per90':>8} {'mean |gap|':>11} {'max |gap|':>10}")
    for g in check.groups:
        print(
            f"     {g.peer_role + f' >={g.min_minutes}':<12} {g.ranked_on_both:>6} "
            f"{_num(g.correlation):>8} {_num(g.mean_abs_gap, 1):>11} {_num(g.max_abs_gap, 1):>10}"
        )
    print("   biggest movers per group (xA per90 / pct -> xAG per90 / pct):")
    if not any(g.movers for g in check.groups):
        print("     none: both rank every player identically")
    for g in check.groups:
        for m in g.movers:
            print(
                f"     {g.peer_role:<3} {m.name:<26} {m.team:<22} {m.xa_per90:.3f} / "
                f"{m.xa_pct:>4.1f} -> {m.xag_per90:.3f} / {m.xag_pct:>4.1f}  ({m.gap:+.1f})"
            )


def print_xa_definition(check: XaDefinitionCheck) -> None:
    print("5. Is /players' expected_assists real xA? (xA credits passes with no shot; xAG can't)")
    print(
        f"   {check.rows} outfield rows with xa, xag and both chance counts · xA total "
        f"{check.total_xa:.2f}"
    )
    for row in check.no_chance:
        print(
            f"   {row.count_code} = 0: {row.rows} rows · xa > 0 in {row.xa_positive} "
            f"({_share(row.xa_positive, row.rows)}) · xag > 0 in {row.xag_positive} "
            f"({_share(row.xag_positive, row.rows)}) · share of season xA "
            f"{'n/a' if row.xa_share is None else f'{row.xa_share:.1%}'}"
        )
    print("   (real xA: xa > 0 in many no-chance rows; shot-based: about as rare as for xag)")


SECTIONS = ("1", "2", "3", "4", "5")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--league", required=True, help="PitchAPI league ID, e.g. l_4WFCIZ")
    parser.add_argument("--season", required=True, help='Season label, e.g. "2025/2026"')
    parser.add_argument("--top", type=int, default=20, help="Players listed per slot group")
    parser.add_argument(
        "--only", choices=SECTIONS, action="append", help="Run only these checks (repeatable)"
    )
    args = parser.parse_args()
    sections = set(args.only or SECTIONS)

    engine = make_engine(Settings().database_url)
    try:
        check_schema_current(engine)
        with Session(engine) as session:
            season_id = find_season(session, args.league, args.season).id
            print(f"slice 2c checks · {args.league} {args.season}")
            if "1" in sections:
                print()
                print_fouled(check_was_fouled(session, season_id))
            if "2" in sections:
                print("\n2. Keeper rates vs their counts (per keeper appearance)")
                for spec in RATE_SPECS:
                    print_rate(check_rate(session, season_id, spec))
            if "3" in sections:
                print("\n3. Role judgement calls: starters in the disputed slots")
                for slot_spec in SLOT_SPECS:
                    print_slots(check_slots(session, season_id, slot_spec), args.top)
            if "4" in sections:
                print()
                print_xa(check_xa_xag(session, season_id))
            if "5" in sections:
                print()
                print_xa_definition(check_xa_definition(session, season_id))
            session.rollback()  # read-only: nothing to keep
    except (CheckError, SyncError, SchemaOutOfDateError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
