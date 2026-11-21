"""Slice 2d spike: what do PitchAPI's heatmaps and pass networks actually look like?

Throwaway code (outside the package), like the Phase 0 spike. It answers the go/no-go
questions for the 6/8 split (D-016) before anything is designed on them:
  1. Shape: is there a heatmap per PLAYER, or only per team?
  2. Direction: in the default frame, does every team attack the same way (keepers deep)?
  3. Depth: do the user's anchor pairs come out the right way round
     (Caicedo deeper than Enzo, Adams deeper than Scott)?
  4. What counts as an "action" (compared with /players touches)?
  5. Does the pass network carry average positions (plan B if heatmaps are team-only)?
  6. Cost: how many requests per match?

Run from backend/ (the API key comes from .env through Settings, as for `scouting sync`):
    uv run python spikes/heatmap_spike.py run
    uv run python spikes/heatmap_spike.py analyze

`run` picks 3 EPL 2025/26 matches from scouting.db (read-only), fetches heatmaps (default
frame and frame=home_ltr) and the pass network for each: 9 read-only GETs at 1 request/s,
saved under spikes/data/ (gitignored, D-007). Files already saved are skipped, so a re-run
costs nothing unless you pass --refresh. Then it analyzes. `analyze` is fully offline.

Nothing here assumes the response shape beyond the docs' "[cell_x, cell_y, actions]"
triples: it walks the JSON, prints its structure, and reports what it finds.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Engine, and_, select
from sqlalchemy.orm import Session

from scouting.adapters.pitchapi.client import ApiResponse, PitchApiClient, build_path
from scouting.adapters.pitchapi.errors import PitchApiAuthError, PitchApiError
from scouting.config.settings import Settings
from scouting.db.base import make_engine
from scouting.db.models import (
    Appearance,
    Match,
    MatchSourceId,
    Metric,
    Player,
    PlayerSourceId,
    StatValue,
    Team,
)
from scouting.ingest.season import find_season

Json = Any

DATA_DIR: Final = Path(__file__).resolve().parent / "data"
PREFIX: Final = "heat_"
DEFAULT_LEAGUE: Final = "l_4WFCIZ"
DEFAULT_SEASON: Final = "2025/2026"

# The user's anchor pairs (6 then 8) from the same team, and a single-pivot formation
# for a textbook 6. Names are matched as substrings of player.search_name (unidecoded).
ANCHOR_PAIRS: Final = (
    ("chelsea", "caicedo", "enzo fernandez"),
    ("bournemouth", "tyler adams", "alex scott"),
)
SINGLE_PIVOT: Final = ("4-1-4-1", "4-1-2-3", "4-1-3-2")
CENTRAL_MIDS: Final = ("DM", "CM")

# (file suffix, path segments after /matches/{id}/, query params)
ENDPOINTS: Final[tuple[tuple[str, tuple[str, ...], dict[str, str] | None], ...]] = (
    ("heatmaps", ("heatmaps",), None),
    ("heatmaps-home-ltr", ("heatmaps",), {"frame": "home_ltr"}),
    ("network", ("advanced", "network"), None),
)

GRID_DOCS: Final = (16, 12)  # cells along (first index, second index), per the docs
# PitchAPI IDs are prefix + "_" + 6 base62 chars; a looser length only helps detection.
ID_RE: Final = re.compile(r"^[pt]_[A-Za-z0-9]{3,12}$")
MAX_STRUCTURE_LINES: Final = 70


# --- picking matches (read-only on scouting.db) -------------------------------------------


@dataclass(frozen=True, slots=True)
class Pick:
    match_ext: str
    reason: str


def pick_matches(session: Session, season_id: int) -> list[Pick]:
    rows = session.execute(
        select(
            MatchSourceId.external_id,
            Match.match_date,
            Team.name,
            Player.search_name,
            Appearance.role,
            Appearance.formation,
        )
        .join(Match, Match.id == Appearance.match_id)
        .join(MatchSourceId, MatchSourceId.match_id == Match.id)
        .join(Team, Team.id == Appearance.team_id)
        .join(Player, Player.id == Appearance.player_id)
        .where(
            Match.season_id == season_id,
            Appearance.is_starter.is_(True),
            Appearance.role.in_(CENTRAL_MIDS),
        )
        .order_by(Match.match_date, MatchSourceId.external_id)
    ).all()

    picks: list[Pick] = []
    for team_sub, six, eight in ANCHOR_PAIRS:
        names: dict[tuple[str, str], set[str]] = defaultdict(set)
        for ext, _, team, search_name, _, _ in rows:
            if team_sub in team.lower():
                names[(ext, team)].add(search_name)
        found = next(
            (
                (ext, team)
                for (ext, team), present in names.items()
                if any(six in n for n in present) and any(eight in n for n in present)
            ),
            None,
        )
        if found is None:
            print(f"note: no match where {six!r} and {eight!r} both started in midfield")
        else:
            picks.append(Pick(found[0], f"{found[1]}: {six} (6) and {eight} (8) both start"))

    taken = {p.match_ext for p in picks}
    pivot = next(
        (
            (ext, team, formation)
            for ext, _, team, _, role, formation in rows
            if formation in SINGLE_PIVOT and role == "DM" and ext not in taken
        ),
        None,
    )
    if pivot is not None:
        picks.append(Pick(pivot[0], f"{pivot[1]} in a {pivot[2]}: a single pivot (a pure 6)"))
    return picks


# --- fetching -----------------------------------------------------------------------------


def file_for(match_ext: str, suffix: str) -> Path:
    return DATA_DIR / f"{PREFIX}{match_ext}_{suffix}.json"


def save(path: Path, record: dict[str, Any]) -> None:
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False))


def record_of(response: ApiResponse) -> dict[str, Any]:
    return {
        "path": response.path,
        "params": dict(response.params),
        "status": response.status,
        "fetched_at": response.fetched_at.isoformat(),
        "data": response.data,
        "error": None
        if response.ok
        else {"code": response.error_code, "message": response.error_message},
    }


def fetch_all(settings: Settings, picks: list[Pick], refresh: bool) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    requests = 0
    with PitchApiClient(
        settings.require_api_key(), min_interval_s=settings.min_request_interval_s
    ) as client:
        for pick in picks:
            for suffix, segments, params in ENDPOINTS:
                target = file_for(pick.match_ext, suffix)
                if target.exists() and not refresh:
                    print(f"skip  {target.name} (saved)")
                    continue
                path = build_path("matches", pick.match_ext, *segments)
                requests += 1
                try:
                    record = record_of(client.get(path, params))
                except PitchApiAuthError:
                    raise
                except PitchApiError as exc:  # e.g. 400 for an unknown `frame`: keep going
                    error = {"code": exc.code, "message": str(exc)}
                    record = {
                        "path": path,
                        "params": params or {},
                        "status": exc.status,
                        "data": None,
                        "error": error,
                    }
                save(target, record)
                outcome = "ok" if record["error"] is None else record["error"]["code"]
                print(f"saved {target.name}  HTTP {record['status']} {outcome}")
    return requests


# --- generic JSON exploration -------------------------------------------------------------


def is_cell(value: Json) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(v, int | float) and not isinstance(v, bool) for v in value)
    )


def is_grid(value: Json) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(is_cell(v) for v in value)


def type_name(value: Json) -> str:
    if value is None:
        return "null"
    if is_grid(value):
        return f"cells[{len(value)}] of [x, y, n]"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"object({len(value)} keys)"
    return type(value).__name__


def structure(value: Json, path: str = "data", depth: int = 0) -> Iterator[str]:
    """One line per distinct path; lists show their first element only; ID-like keys
    (p_xxxxxx) are collapsed to <id> so a per-player dict prints once."""
    example = f" = {value!r}"[:60] if isinstance(value, str | int | float | bool) else ""
    yield f"{'  ' * depth}{path}: {type_name(value)}{example}"
    if depth >= 6 or is_grid(value):
        return
    if isinstance(value, dict):
        seen_id_key = False
        for key, child in value.items():
            if ID_RE.match(str(key)):
                if seen_id_key:
                    continue
                seen_id_key = True
                key = "<id>"
            yield from structure(child, str(key), depth + 1)
    elif isinstance(value, list) and value:
        yield from structure(value[0], "[0]", depth + 1)


@dataclass(slots=True)
class Grid:
    path: str
    player: str | None  # p_ ID that owns this grid, if any
    team: str | None  # t_ ID nearest to it, if any
    cells: list[list[float]]


def ids_in(obj: dict[str, Json]) -> tuple[str, ...]:
    """ID-like strings among an object's values and its child objects' values
    (so both {"player_id": "p_.."} and {"player": {"id": "p_.."}} count)."""
    found: list[str] = []
    for value in obj.values():
        children = value.values() if isinstance(value, dict) else (value,)
        found.extend(v for v in children if isinstance(v, str) and ID_RE.match(v))
    return tuple(found)


def find_grids(value: Json, path: str = "data", ids: tuple[str, ...] = ()) -> Iterator[Grid]:
    """Every [x, y, n] cell list, with the IDs found on the way to it: ID-like dict keys,
    and ID-like string values of the dicts it sits in (nearest last)."""
    if is_grid(value):
        player = next((i for i in reversed(ids) if i.startswith("p_")), None)
        team = next((i for i in reversed(ids) if i.startswith("t_")), None)
        yield Grid(path, player, team, value)
        return
    if isinstance(value, dict):
        here = ids_in(value)
        for key, child in value.items():
            key_id = (str(key),) if ID_RE.match(str(key)) else ()
            yield from find_grids(child, f"{path}.{key}", ids + here + key_id)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from find_grids(child, f"{path}[{i}]", ids)


def numbers_in(obj: dict[str, Json]) -> dict[str, float]:
    """Numeric fields of an object and of its child objects ("position.x")."""
    numbers: dict[str, float] = {}
    for key, value in obj.items():
        children = value.items() if isinstance(value, dict) else ((None, value),)
        for sub, v in children:
            if isinstance(v, int | float) and not isinstance(v, bool):
                numbers[f"{key}.{sub}" if sub else str(key)] = float(v)
    return numbers


def find_nodes(value: Json) -> Iterator[tuple[str, dict[str, float]]]:
    """Objects that carry a p_ ID and some numbers: candidate per-player network nodes."""
    if isinstance(value, dict):
        player = next((v for v in ids_in(value) if v.startswith("p_")), None)
        numbers = numbers_in(value)
        if player is not None and len(numbers) >= 2:
            yield player, numbers
        for child in value.values():
            yield from find_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from find_nodes(child)


# --- depth --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Depth:
    actions: float
    x: float  # 0 = own goal line .. 1 = opponent's, if the frame is "attacking left to right"
    y: float


def depth_of(cells: list[list[float]]) -> Depth | None:
    total = sum(c[2] for c in cells)
    if total <= 0:
        return None
    x = sum((c[0] + 0.5) * c[2] for c in cells) / total / GRID_DOCS[0]
    y = sum((c[1] + 0.5) * c[2] for c in cells) / total / GRID_DOCS[1]
    return Depth(total, x, y)


@dataclass(slots=True)
class Who:
    name: str
    search_name: str
    team: str
    role: str | None
    slot: int | None
    starter: bool | None
    goalkeeper: bool
    minutes: int
    touches: float | None = None


def who_played(session: Session, match_ext: str) -> dict[str, Who]:
    """p_ ID -> what scouting.db knows about that player in this match."""
    touches_id = session.scalar(select(Metric.id).where(Metric.code == "touches"))
    rows = session.execute(
        select(
            PlayerSourceId.external_id,
            Player.name,
            Player.search_name,
            Team.name,
            Appearance.role,
            Appearance.slot_code,
            Appearance.is_starter,
            Appearance.is_goalkeeper,
            Appearance.minutes,
            StatValue.value,
        )
        .join(Player, Player.id == Appearance.player_id)
        .join(PlayerSourceId, PlayerSourceId.player_id == Player.id)
        .join(Team, Team.id == Appearance.team_id)
        .join(MatchSourceId, MatchSourceId.match_id == Appearance.match_id)
        .outerjoin(
            StatValue,
            and_(StatValue.appearance_id == Appearance.id, StatValue.metric_id == touches_id),
        )
        .where(MatchSourceId.external_id == match_ext)
    )
    return {
        ext: Who(name, search, team, role, slot, starter, keeper, minutes, touches)
        for ext, name, search, team, role, slot, starter, keeper, minutes, touches in rows
    }


def load(match_ext: str, suffix: str) -> dict[str, Any] | None:
    path = file_for(match_ext, suffix)
    if not path.exists():
        return None
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


def saved_matches() -> list[str]:
    names = sorted(DATA_DIR.glob(f"{PREFIX}*_heatmaps.json"))
    return [p.name[len(PREFIX) : -len("_heatmaps.json")] for p in names]


def analyze_match(session: Session, match_ext: str) -> None:
    print(f"\n{'=' * 100}\nmatch {match_ext}")
    players = who_played(session, match_ext)
    teams = sorted({w.team for w in players.values()})
    print(f"  in scouting.db: {len(players)} appearances ({' v '.join(teams) or 'not found'})")

    per_frame: dict[str, dict[str, Depth]] = {}
    for suffix, _, _ in ENDPOINTS:
        record = load(match_ext, suffix)
        print(f"\n--- {suffix}")
        if record is None:
            print("  not fetched")
            continue
        print(
            f"  GET {record['path']} {record.get('params') or ''} -> HTTP {record['status']}"
            + (f" {record['error']}" if record["error"] else "")
        )
        if record["data"] is None:
            continue
        lines = list(structure(record["data"]))
        for line in lines[:MAX_STRUCTURE_LINES]:
            print(f"  {line}")
        if len(lines) > MAX_STRUCTURE_LINES:
            print(f"  ... {len(lines) - MAX_STRUCTURE_LINES} more structure lines")
        if suffix.startswith("heatmaps"):
            per_frame[suffix] = summarize_grids(record["data"], players)
        else:
            summarize_network(record["data"], players, per_frame.get("heatmaps", {}))

    if per_frame.get("heatmaps"):
        print_depth_table(players, per_frame["heatmaps"])
        print_direction(players, per_frame)
        print_anchors(players, per_frame["heatmaps"])


def summarize_grids(data: Json, players: dict[str, Who]) -> dict[str, Depth]:
    grids = list(find_grids(data))
    if not grids:
        print("  no [x, y, n] cell lists found: see the structure above")
        return {}
    owned = [g for g in grids if g.player]
    team_only = [g for g in grids if not g.player]
    xs = [c[0] for g in grids for c in g.cells]
    ys = [c[1] for g in grids for c in g.cells]
    print(
        f"  {len(grids)} cell lists: {len(owned)} with a player ID, {len(team_only)} without"
        f" · first index {min(xs):g}..{max(xs):g}, second {min(ys):g}..{max(ys):g}"
        f" (docs: {GRID_DOCS[0]} x {GRID_DOCS[1]})"
    )
    if team_only:
        print(f"  e.g. no player ID at: {team_only[0].path} (team {team_only[0].team})")
    depths: dict[str, Depth] = {}
    for g in owned:
        assert g.player is not None
        d = depth_of(g.cells)
        if d is not None:
            depths[g.player] = d  # one grid per player expected; a later one would show here
    missing = [w.name for p, w in players.items() if p not in depths]
    matched = sum(1 for p in depths if p in players)
    strangers = len(depths) - matched
    print(
        f"  players with a grid: {matched} of {len(players)} appearances"
        + (f" (+{strangers} grids for IDs not in this match)" if strangers else "")
        + (f" · none for: {', '.join(sorted(missing))}" if missing else "")
    )
    ratios = [
        d.actions / touches
        for p, d in depths.items()
        if p in players and (touches := players[p].touches)
    ]
    if ratios:
        print(
            f"  actions / touches (from /players): median {statistics.median(ratios):.2f}, "
            f"range {min(ratios):.2f}..{max(ratios):.2f} over {len(ratios)} players"
        )
    return depths


def print_depth_table(players: dict[str, Who], depths: dict[str, Depth]) -> None:
    print(
        "\n--- depth per player, default frame (x: 0 = own goal .. 1 = opponent's, if the frame"
        " is 'own team attacking left to right'; rel = x minus team outfield mean)"
    )
    by_team: dict[str, list[tuple[str, Who, Depth]]] = defaultdict(list)
    for pid, d in depths.items():
        w = players.get(pid)
        if w is not None:
            by_team[w.team].append((pid, w, d))
    for team, rows in sorted(by_team.items()):
        outfield = [(w, d) for _, w, d in rows if not w.goalkeeper]
        total = sum(d.actions for _, d in outfield)
        mean = sum(d.x * d.actions for _, d in outfield) / total if total else 0.0
        mids = sorted((d.x, w.name) for _, w, d in rows if w.starter and w.role in CENTRAL_MIDS)
        rank = {name: i + 1 for i, (_, name) in enumerate(mids)}
        print(f"  {team} (outfield mean x {mean:.3f})")
        print(
            f"    {'player':<26} {'role':<4} {'slot':>4} {'min':>4} {'actions':>7}"
            f" {'touches':>7} {'x':>6} {'rel':>7} {'y':>6}  mid depth rank"
        )
        for _, w, d in sorted(rows, key=lambda r: r[2].x):
            touches = "-" if w.touches is None else f"{w.touches:g}"
            mid_rank = f"{rank[w.name]} of {len(mids)}" if w.name in rank else ""
            print(
                f"    {w.name:<26} {w.role or 'sub':<4} {w.slot or '':>4} {w.minutes:>4}"
                f" {d.actions:>7g} {touches:>7} {d.x:>6.3f} {d.x - mean:>+7.3f} {d.y:>6.3f}"
                f"  {mid_rank}"
            )


def print_direction(players: dict[str, Who], per_frame: dict[str, dict[str, Depth]]) -> None:
    print("\n--- direction check: keepers' and strikers' mean x per frame")
    for frame, depths in per_frame.items():
        for role_name in ("GK", "ST"):
            parts = [
                f"{w.team} {w.name} {d.x:.3f}"
                for p, d in depths.items()
                if (w := players.get(p)) is not None
                and (w.goalkeeper if role_name == "GK" else w.role == "ST")
            ]
            print(f"  {frame:<18} {role_name}: {' · '.join(parts) or 'none'}")


def print_anchors(players: dict[str, Who], depths: dict[str, Depth]) -> None:
    for team_sub, six, eight in ANCHOR_PAIRS:
        found = []
        for name in (six, eight):
            found.append(
                next(
                    (
                        (w, depths[p])
                        for p, w in players.items()
                        if name in w.search_name and team_sub in w.team.lower() and p in depths
                    ),
                    None,
                )
            )
        a, b = found
        if a is None or b is None:
            continue
        gap = a[1].x - b[1].x
        verdict = "the 6 is deeper ✓" if gap < 0 else "the 6 is NOT deeper ✗"
        print(
            f"\n--- anchor pair: {a[0].name} (6) x {a[1].x:.3f} vs {b[0].name} (8) x {b[1].x:.3f}"
            f" → gap {gap:+.3f}: {verdict}"
        )


def summarize_network(data: Json, players: dict[str, Who], depths: dict[str, Depth]) -> None:
    nodes = list(find_nodes(data))
    if not nodes:
        print("  no objects with a player ID and numbers: see the structure above")
        return
    fields = sorted({k for _, nums in nodes for k in nums})
    distinct = len({p for p, _ in nodes})
    print(
        f"  {len(nodes)} objects with a player ID ({distinct} players) · numeric fields:"
        f" {', '.join(fields)}"
    )
    if not depths:
        return
    print("  correlation of each numeric field with heatmap depth x (same players):")
    first: dict[str, dict[str, float]] = {}
    for pid, nums in nodes:
        first.setdefault(pid, nums)
    for name in fields:
        pairs = [
            (nums[name], depths[p].x) for p, nums in first.items() if name in nums and p in depths
        ]
        if len(pairs) < 3:
            continue
        try:
            r = statistics.correlation([a for a, _ in pairs], [b for _, b in pairs])
        except statistics.StatisticsError:
            continue
        print(f"    {name:<24} r = {r:+.2f} over {len(pairs)} players")


# --- CLI ----------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("command", choices=("run", "analyze"))
    parser.add_argument("--league", default=DEFAULT_LEAGUE)
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--match", action="append", default=[], help="extra match ID(s)")
    parser.add_argument("--refresh", action="store_true", help="re-fetch saved responses")
    args = parser.parse_args()

    settings = Settings()
    engine: Engine = make_engine(settings.database_url)
    try:
        with Session(engine) as session:
            if args.command == "run":
                season_id = find_season(session, args.league, args.season).id
                picks = pick_matches(session, season_id) + [
                    Pick(m, "requested with --match") for m in args.match
                ]
                print("matches:")
                for pick in picks:
                    print(f"  {pick.match_ext}  {pick.reason}")
                requests = fetch_all(settings, picks, args.refresh)
                print(f"requests to PitchAPI this run: {requests}")
            matches = saved_matches()
            if not matches:
                print("nothing saved yet: run `run` first", file=sys.stderr)
                return 1
            for match_ext in matches:
                analyze_match(session, match_ext)
            session.rollback()  # read-only
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
