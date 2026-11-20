"""Phase 0 spike cross-checks (offline). Run from backend/: uv run python spikes/spike_checks.py"""

import collections
import glob
import json

D = "spikes/data/"


def flat(row):
    """Stat key -> value for one /players row; first occurrence wins, conflicts reported."""
    out, conflicts = {}, []
    for group in row.get("stats") or []:
        for entry in (group.get("stats") or {}).values():
            key = entry.get("key")
            if not key or entry["stat"].get("type") == "boolean":
                continue
            value = entry["stat"].get("value")
            if key in out and out[key] != value:
                conflicts.append((key, out[key], value))
            out.setdefault(key, value)
    return out, conflicts


players, adv = {}, {}
for f in sorted(glob.glob(D + "match_*_players.json")):
    if f.endswith("_advanced-players.json"):
        continue
    mid = f.split("match_")[1].rsplit("_players", 1)[0]
    players[mid] = json.load(open(f))["data"]
for f in sorted(glob.glob(D + "match_*_advanced-players.json")):
    mid = f.split("match_")[1].rsplit("_advanced-players", 1)[0]
    adv[mid] = {r["player"]["id"]: r for r in json.load(open(f))["data"]["players"]}

played, unused, all_conflicts = [], 0, []
for mid, rows in players.items():
    for r in rows:
        s, c = flat(r)
        all_conflicts += c
        if "minutes_played" in s:
            played.append((mid, r, s))
        else:
            unused += 1
print(f"A. /players rows: {len(played)} played, {unused} without minutes (unused subs?)")
print(f"   duplicate keys with conflicting values across groups: {all_conflicts or 'none'}")

outfield = [p for p in played if "saves" not in p[2]]
print(f"\nB. Sparsity per key, {len(outfield)} played outfield rows")
print("   present-with-0 = 0 and many absent  => key is omitted when zero")
keys = sorted({k for _, _, s in outfield for k in s})
print(f"   {'key':<36}{'present':>8}{'with 0':>8}{'null':>6}{'absent':>8}")
for k in keys:
    vals = [s[k] for _, _, s in outfield if k in s]
    zeros = sum(1 for v in vals if v == 0)
    nulls = sum(1 for v in vals if v is None)
    print(f"   {k:<36}{len(vals):>8}{zeros:>8}{nulls:>6}{len(outfield) - len(vals):>8}")

bad = [(m, r["player"]["name"]) for m, r, s in outfield if (s.get("goals") or 0) > 0 and "total_shots" not in s]
print(f"\nC. Scorers with no total_shots key (should be none): {bad or 'none'}")

no_rating = sorted(s["minutes_played"] for _, _, s in played if "rating_title" not in s)
print(f"\nD. Played rows without rating: {len(no_rating)}; their minutes: {no_rating}")

pairs = {
    "tackles": ("matchstats.headers.tackles", ("defending", "tackles")),
    "interceptions": ("interceptions", ("defending", "interceptions")),
    "clearances": ("clearances", ("defending", "clearances")),
    "assists": ("assists", ("passing", "assists")),
    "chances_created": ("chances_created", ("creation", "chances_created")),
    "dispossessed": ("dispossessed", ("carrying", "dispossessed")),
    "minutes": ("minutes_played", (None, "minutes_played")),
}
print("\nE. /players vs /advanced/players for the same player (absent /players key counted as 0)")
for name, (pkey, (grp, akey)) in pairs.items():
    same = diff = missing_adv = 0
    examples = []
    for mid, r, s in played:
        a = adv.get(mid, {}).get(r["player"]["id"])
        if a is None:
            missing_adv += 1
            continue
        av = a[akey] if grp is None else (a.get(grp) or {}).get(akey)
        pv = s.get(pkey, 0)
        if pv == av:
            same += 1
        else:
            diff += 1
            if len(examples) < 5:
                examples.append((r["player"]["name"], pv, av))
    print(f"   {name:<16} same {same:>4}  differ {diff:>4}  no advanced row {missing_adv:>3}  e.g. {examples}")