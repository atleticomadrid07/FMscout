"""Phase 0 spike: fetch a small real sample from PitchAPI and answer the open questions.

Throwaway code (lives outside the package). Fetch subcommands make read-only GETs
at 1 request/s and save every response under spikes/data/ (gitignored). Files that
already exist are skipped, so re-running costs no requests unless you pass
--refresh. `analyze` is fully offline.

Run from backend/ (.env at the repo root; use .env if yours is in backend/):
    uv run --env-file ../.env python spikes/pitchapi_spike.py leagues
    uv run --env-file ../.env python spikes/pitchapi_spike.py matches \
        --league l_xxxxxx --season 2025/2026
    uv run --env-file ../.env python spikes/pitchapi_spike.py match m_aaaaaa m_bbbbbb
    uv run --env-file ../.env python spikes/pitchapi_spike.py profiles p_aaaaaa p_bbbbbb
    uv run python spikes/pitchapi_spike.py analyze > spikes/data/analysis.txt

The analyzer only relies on the shapes documented in PITCHAPI_NOTES.md (stat entries
carry `key` + `type`; advanced rows have `creation`/`passing` groups; lineup rows
carry `pitch_x`/`pitch_y`). Everything else is discovered by walking the JSON, so
it reports what is actually there instead of assuming field names.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from scouting.adapters.pitchapi.client import ApiResponse, PitchApiClient, build_path

DATA_DIR = Path(__file__).resolve().parent / "data"
API_KEY_ENV = "PITCHAPI_API_KEY"
MATCH_ENDPOINTS = (("players",), ("advanced", "players"), ("lineups",))
MAX_EXAMPLES = 15

Json = Any


# --- fetching ---------------------------------------------------------------------------


def slug(*parts: str) -> str:
    return "_".join(re.sub(r"[^A-Za-z0-9]+", "-", p).strip("-") for p in parts)


def save(name: str, response: ApiResponse) -> None:
    record = {
        "path": response.path,
        "params": dict(response.params),
        "status": response.status,
        "fetched_at": response.fetched_at.isoformat(),
        "data": response.data,
        "error": None
        if response.ok
        else {"code": response.error_code, "message": response.error_message},
    }
    (DATA_DIR / f"{name}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))


def fetch(
    client: PitchApiClient,
    name: str,
    path: str,
    params: dict[str, str] | None,
    refresh: bool,
) -> None:
    if (DATA_DIR / f"{name}.json").exists() and not refresh:
        print(f"skip  {name} (cached)")
        return
    response = client.get(path, params)
    save(name, response)
    outcome = "ok" if response.ok else response.error_code
    print(f"saved {name}  HTTP {response.status} {outcome}")


def scalars(obj: Json) -> Json:
    """Top-level non-container fields of an object, for compact printing."""
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if not isinstance(v, dict | list)}
    return obj


def unwrap(data: Json) -> tuple[list[Json], dict[str, Json]]:
    """(items, extras) for a bare list or an object wrapping one list, e.g. {"leagues": [...]}.

    `extras` are the wrapper's other fields, printed so pagination fields can't hide.
    """
    if isinstance(data, list):
        return data, {}
    if isinstance(data, dict):
        lists = {k: v for k, v in data.items() if isinstance(v, list)}
        if len(lists) == 1:
            (items,) = lists.values()
            return items, {k: v for k, v in data.items() if k not in lists}
    return [data], {}


def print_listing(name: str) -> None:
    items, extras = unwrap(load(name)["data"])
    print(f"\n{name}: {len(items)} item(s). Keys of the first: {sorted(first_keys(items))}")
    if extras:
        print(
            f"other fields next to the list (pagination?): {json.dumps(extras, ensure_ascii=False)}"
        )
    print()
    for item in items:
        extra = {"seasons": item["seasons"]} if isinstance(item, dict) and "seasons" in item else {}
        print(json.dumps(scalars(item) | extra, ensure_ascii=False))


def first_keys(items: list[Json]) -> list[str]:
    return list(items[0].keys()) if items and isinstance(items[0], dict) else []


def run_fetch(args: argparse.Namespace) -> None:
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        sys.exit(f"{API_KEY_ENV} is not set (try: uv run --env-file ../.env ...)")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with PitchApiClient(api_key) as client:
        if args.command == "leagues":
            fetch(client, "leagues", build_path("leagues"), None, args.refresh)
            print_listing("leagues")
        elif args.command == "matches":
            name = f"matches_{slug(args.league, args.season, args.status)}"
            params = {"season": args.season, "status": args.status}
            fetch(client, name, build_path("leagues", args.league, "matches"), params, args.refresh)
            print_listing(name)
        elif args.command == "match":
            for match_id in args.match_ids:
                for endpoint in MATCH_ENDPOINTS:
                    name = f"match_{slug(match_id)}_{'-'.join(endpoint)}"
                    fetch(
                        client, name, build_path("matches", match_id, *endpoint), None, args.refresh
                    )
        elif args.command == "profiles":
            for player_id in args.player_ids:
                fetch(
                    client,
                    f"player_{slug(player_id)}",
                    build_path("players", player_id),
                    None,
                    args.refresh,
                )


# --- analysis helpers -------------------------------------------------------------------


def load(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((DATA_DIR / f"{name}.json").read_text())
    return loaded


def files(prefix: str, suffix: str = "") -> list[str]:
    return sorted(p.stem for p in DATA_DIR.glob(f"{prefix}*.json") if p.stem.endswith(suffix))


def walk(node: Json, path: str = "$") -> Iterator[tuple[str, Json]]:
    """Yield (path_pattern, node) for every node; list indices collapse to []."""
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for item in node:
            yield from walk(item, f"{path}[]")


def flatten(obj: Json, prefix: str = "") -> dict[str, Json]:
    """Leaf paths of nested dicts. Lists are kept as leaves (we only inventory them)."""
    out: dict[str, Json] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else key))
    else:
        out[prefix] = obj
    return out


def stat_entries(node: Json, group: str = "?") -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (group key, entry) for /players stat entries.

    Real shape (spike): {"key": "top_stats", "stats": {"<Label>": {"key": str, "stat":
    {"type": str, "value": ..., "total"?: ...}}}}. The docs describe key/type on one level;
    the response nests them, so an entry is any dict with a string `key` and a `stat` dict
    that has a string `type`.
    """
    if isinstance(node, dict):
        stat = node.get("stat")
        if (
            isinstance(node.get("key"), str)
            and isinstance(stat, dict)
            and isinstance(stat.get("type"), str)
        ):
            yield group, node
            return
        own = node.get("key")
        next_group = own if isinstance(own, str) else group
        for value in node.values():
            yield from stat_entries(value, next_group)
    elif isinstance(node, list):
        for item in node:
            yield from stat_entries(item, group)


def rows_with(node: Json, *required: str) -> Iterator[dict[str, Any]]:
    """Every dict that has all of `required` as keys."""
    for _, value in walk(node):
        if isinstance(value, dict) and all(k in value for k in required):
            yield value


def identity(row: dict[str, Any]) -> str:
    """Best-effort label for a row: id/name-like fields at the top level or under `player`."""
    player = row.get("player")
    sources: list[dict[str, Any]] = [row, player] if isinstance(player, dict) else [row]
    found = {
        k: v
        for src in sources
        for k, v in src.items()
        if isinstance(v, str | int) and re.search(r"(^id$|_id$|name)", k)
    }
    return json.dumps(found, ensure_ascii=False)


def is_number(value: Json) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def section(title: str) -> None:
    print(f"\n{'=' * 80}\n{title}\n{'=' * 80}")


# --- analyses ---------------------------------------------------------------------------


def analyze_structure() -> None:
    section("0. Response shapes (path patterns, first file of each kind)")
    for kind in ("_players", "_advanced-players", "_lineups"):
        names = files("match_", kind)
        ok = [n for n in names if load(n)["error"] is None]
        if not ok:
            print(f"{kind}: no successful files")
            continue
        patterns = sorted({p for p, v in walk(load(ok[0])["data"]) if isinstance(v, dict | list)})
        print(f"\n{kind} ({ok[0]}):")
        for pattern in patterns[:60]:
            print(f"  {pattern}")
        if len(patterns) > 60:
            print(f"  ... {len(patterns) - 60} more")


def analyze_stat_keys() -> None:
    section("1. /players stat key inventory: group / key / type, presence, nulls")
    seen: Counter[tuple[str, str, str]] = Counter()
    nulls: Counter[tuple[str, str, str]] = Counter()
    stat_fields: Counter[str] = Counter()
    groups_per_key: dict[str, set[str]] = defaultdict(set)
    player_rows = 0
    for name in files("match_", "_players"):
        record = load(name)
        if record["error"]:
            continue
        player_rows += len(record["data"]) if isinstance(record["data"], list) else 0
        for group, entry in stat_entries(record["data"]):
            stat = entry["stat"]
            key = entry["key"] or "<empty>"
            ident = (group, key, stat["type"])
            seen[ident] += 1
            groups_per_key[key].add(group)
            if stat.get("value") is None:
                nulls[ident] += 1
            stat_fields.update(stat.keys())
    print(f"player rows: {player_rows}")
    print(f"fields seen inside `stat`: {dict(stat_fields)}\n")
    print(f"{'group':<18} {'key':<40} {'type':<24} {'count':>6} {'null':>5}")
    for ident in sorted(seen):
        group, key, typ = ident
        print(f"{group:<18} {key:<40} {typ:<24} {seen[ident]:>6} {nulls[ident]:>5}")
    dupes = {k: sorted(g) for k, g in groups_per_key.items() if len(g) > 1}
    print(f"\nkeys appearing in more than one group (dedupe when parsing): {dupes or 'none'}")


def analyze_advanced() -> list[dict[str, Any]]:
    section("2. /advanced/players field inventory (per player row)")
    rows: list[dict[str, Any]] = []
    for name in files("match_", "_advanced-players"):
        record = load(name)
        if record["error"]:
            continue
        rows.extend(rows_with(record["data"], "creation", "passing"))
    if not rows:
        print("no advanced rows found")
        return rows
    present: Counter[str] = Counter()
    null: Counter[str] = Counter()
    kinds: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for path, value in flatten(row).items():
            present[path] += 1
            kinds[path].add(type(value).__name__)
            if value is None:
                null[path] += 1
    total = len(rows)
    print(f"{total} player rows\n")
    print(f"{'field':<48} {'present':>8} {'null':>6} {'missing':>8}  types")
    for path in sorted(present):
        missing = total - present[path]
        types = ",".join(sorted(kinds[path]))
        print(f"{path:<48} {present[path]:>8} {null[path]:>6} {missing:>8}  {types}")
    return rows


def analyze_chances_created(rows: list[dict[str, Any]]) -> None:
    section("3. chances_created vs key_passes (vs key_passes + any assist-like field)")
    equal = differ = incomplete = 0
    explains: Counter[str] = Counter()
    examples: list[str] = []
    for row in rows:
        creation, passing = row.get("creation") or {}, row.get("passing") or {}
        cc, kp = creation.get("chances_created"), passing.get("key_passes")
        if not (is_number(cc) and is_number(kp)):
            incomplete += 1
            continue
        if cc == kp:
            equal += 1
            continue
        differ += 1
        flat = flatten(row)
        assist_like = {p: v for p, v in flat.items() if "assist" in p.lower() and is_number(v)}
        for path, value in assist_like.items():
            if cc == kp + value:
                explains[path] += 1
        if len(examples) < MAX_EXAMPLES:
            examples.append(f"  cc={cc} kp={kp} assist-like={assist_like} {identity(row)}")
    print(f"equal: {equal}   differ: {differ}   null/missing on either side: {incomplete}")
    if differ:
        print("\ndiffering rows where cc == kp + <field>, by field:")
        for path, count in explains.most_common():
            print(f"  {path}: {count}/{differ}")
        print("\nexamples:")
        print("\n".join(examples))
    print(
        "\nNote: if no advanced field explains the gap, compare against the assists key"
        " from the /players inventory (section 1) for the same players."
    )


def analyze_lineups() -> list[dict[str, Any]]:
    section("4. Lineups: position_id vs pitch_x/pitch_y (full table in lineups.csv)")
    out_rows: list[dict[str, Any]] = []
    for name in files("match_", "_lineups"):
        record = load(name)
        if record["error"]:
            print(f"{name}: {record['error']}")
            continue
        formations = {p: v for p, v in walk(record["data"]) if p.endswith(".formation")}
        print(f"\n{name}  formations: {formations or 'no `formation` field found'}")
        # Track the nearest ancestor that has a formation, so rows group by team.
        stack: list[tuple[Json, Json]] = [(record["data"], None)]
        while stack:
            node, formation = stack.pop()
            if isinstance(node, dict):
                formation = node.get("formation", formation)
                if "pitch_x" in node:
                    out_rows.append(
                        {
                            "file": name,
                            "formation": formation,
                            "position_id": node.get("position_id"),
                            "pitch_x": node.get("pitch_x"),
                            "pitch_y": node.get("pitch_y"),
                            "who": identity(node),
                        }
                    )
                    continue
                stack.extend((v, formation) for v in node.values())
            elif isinstance(node, list):
                stack.extend((v, formation) for v in node)
    if not out_rows:
        print("no rows with pitch_x found")
        return out_rows
    with (DATA_DIR / "lineups.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)

    starters = [r for r in out_rows if r["pitch_x"] or r["pitch_y"]]
    subs = [r for r in out_rows if not (r["pitch_x"] or r["pitch_y"])]
    print(f"\nrows: {len(out_rows)}  (non-zero coords: {len(starters)}, zero coords: {len(subs)})")
    print(f"sub position_id values: {dict(Counter(r['position_id'] for r in subs))}")
    print("\nstarters by formation, position_id (x, y):")
    by_slot: dict[tuple[str, Json], list[str]] = defaultdict(list)
    for r in starters:
        by_slot[(str(r["formation"]), r["position_id"])].append(f"({r['pitch_x']}, {r['pitch_y']})")
    for (formation, pos), coords in sorted(
        by_slot.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))
    ):
        print(f"  {formation:<10} {pos!s:>4}: {' '.join(coords[:8])}")
    return out_rows


def analyze_profiles(lineup_rows: list[dict[str, Any]]) -> None:
    section("5. Player profiles: profile position_id vs lineup slots the player started in")
    slots: dict[str, list[str]] = defaultdict(list)
    for r in lineup_rows:
        who = json.loads(r["who"])
        pid = who.get("player_id") or who.get("id")
        if isinstance(pid, str):
            kind = "start" if (r["pitch_x"] or r["pitch_y"]) else "sub"
            slots[pid].append(f"{r['formation']}:{r['position_id']}({kind})")
    print(f"{'profile pos':>11}  {'player':<34} lineup slots")
    for name in files("player_"):
        record = load(name)
        player_id = record["path"].rsplit("/", 1)[-1]
        if record["error"]:
            print(f"{'-':>11}  {player_id:<34} {record['error']}")
            continue
        # The profile may be wrapped (e.g. {"player": {...}}): use the first dict with position_id.
        profile = next(rows_with(record["data"], "position_id"), None)
        if profile is None:
            print(
                f"{'?':>11}  {player_id:<34} no position_id; keys: {first_keys([record['data']])}"
            )
            continue
        label = f"{player_id} {profile.get('name', '')}".strip()
        print(f"{profile['position_id']!s:>11}  {label:<34} {', '.join(slots[player_id]) or '-'}")
    if files("player_"):
        sample = load(files("player_")[0])["data"]
        print(f"\nfirst profile, raw (to see every field):\n{json.dumps(sample, indent=1)[:1500]}")


def analyze_coverage() -> None:
    section("6. Coverage per match: ratings present? advanced analytics available?")
    match_ids = sorted({n.removeprefix("match_").split("_")[0] for n in files("match_")})
    print(f"{'match':<12} {'player rows w/ rating':>22} {'advanced':>24} {'lineups':>10}")
    for mid in match_ids:
        players, adv, lineups = (
            DATA_DIR / f"match_{mid}_{suffix}.json"
            for suffix in ("players", "advanced-players", "lineups")
        )
        rating = "-"
        if players.exists() and (rec := load(players.stem))["error"] is None:
            rated = sum(
                1
                for _, e in stat_entries(rec["data"])
                if e["key"] == "rating_title" and e["stat"].get("value") is not None
            )
            rating = str(rated)
        adv_status = "-"
        if adv.exists():
            rec = load(adv.stem)
            adv_status = "ok" if rec["error"] is None else rec["error"]["code"]
        lu_status = "-"
        if lineups.exists():
            rec = load(lineups.stem)
            lu_status = "ok" if rec["error"] is None else rec["error"]["code"]
        print(f"{mid:<12} {rating:>22} {adv_status:>24} {lu_status:>10}")


def run_analyze() -> None:
    if not DATA_DIR.exists():
        sys.exit("no spikes/data/ yet; run the fetch commands first")
    analyze_structure()
    analyze_stat_keys()
    rows = analyze_advanced()
    analyze_chances_created(rows)
    lineup_rows = analyze_lineups()
    analyze_profiles(lineup_rows)
    analyze_coverage()


# --- CLI --------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("leagues", "matches", "match", "profiles"):
        p = sub.add_parser(name)
        p.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
        if name == "matches":
            p.add_argument("--league", required=True)
            p.add_argument("--season", required=True, help='e.g. "2025/2026", from `leagues`')
            p.add_argument("--status", default="played", choices=("played", "upcoming", "all"))
        elif name == "match":
            p.add_argument("match_ids", nargs="+")
        elif name == "profiles":
            p.add_argument("player_ids", nargs="+")
    sub.add_parser("analyze")

    args = parser.parse_args()
    if args.command == "analyze":
        run_analyze()
    else:
        run_fetch(args)


if __name__ == "__main__":
    main()