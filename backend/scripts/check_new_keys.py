"""Read-only check of the five /players keys added after the EPL 2025/26 crawl.

For each key: in how many player rows it appears, which values it takes (does a 0 ever
appear, or is the key only sent when it's above 0?), and its stat type. Compared with
`goals`, a key we already treat as "missing means 0".

    uv run python scripts/check_new_keys.py

Reads raw.db only (RAW_DB_PATH); makes no API calls and writes nothing.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from collections import Counter, defaultdict

from scouting.adapters.pitchapi.schemas import PlayersPayload
from scouting.config.settings import Settings

KEYS = ("penalties_won", "conceded_penalties", "missed_penalty", "owngoal", "saved_penalties")
COMPARE = ("goals", "saves")


def main() -> None:
    path = Settings().raw_db_path
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = connection.execute(
        "SELECT body_gz FROM raw_response "
        "WHERE path LIKE '/v1/matches/%/players' AND path NOT LIKE '%/advanced/%' "
        "AND status = 200 AND body_gz IS NOT NULL"
    ).fetchall()

    matches = len(rows)
    player_rows = 0
    values: dict[str, Counter[object]] = defaultdict(Counter)
    types: dict[str, Counter[str]] = defaultdict(Counter)
    with_saves: Counter[str] = Counter()  # rows where the key appears alongside `saves`
    owngoal_goals: Counter[object] = Counter()  # the `goals` value in rows with an own goal

    for (body,) in rows:
        payload = PlayersPayload.model_validate(json.loads(gzip.decompress(body)))
        for row in payload.root:
            player_rows += 1
            flat = row.flat_stats()
            for key in KEYS + COMPARE:
                if key in flat:
                    values[key][flat[key].value] += 1
                    types[key][flat[key].type] += 1
                    if "saves" in flat:
                        with_saves[key] += 1
            if "owngoal" in flat:
                owngoal_goals[flat["goals"].value if "goals" in flat else "(absent)"] += 1

    print(f"{matches} /players responses, {player_rows} player rows\n")
    for key in KEYS + COMPARE:
        present = sum(values[key].values())
        print(f"{key}")
        print(f"  present in {present} rows ({present / player_rows:.1%})")
        print(f"  values: {dict(sorted(values[key].items(), key=str))}")
        print(f"  types: {dict(types[key])}")
        print(f"  rows that also have `saves` (keepers): {with_saves[key]}")
    print(f"\n`goals` in rows with an own goal: {dict(owngoal_goals)}")


if __name__ == "__main__":
    main()
