# SCHEMA.md: Canonical data model (v1.1, approved)

**Status:** APPROVED 2026-11-19 (D-011), with the answers to §11 folded in. Approving it closed Phase 0. Further changes go through DECISIONS.md.
**Based on:** the spike findings in [PITCHAPI_NOTES.md](PITCHAPI_NOTES.md) and decisions D-001 to D-010.
**Not code.** Phase 1 turns §5 into SQLAlchemy models and Alembic migrations; §6–§8 are built in Phases 2 and 6–7.

## 1. What the model has to guarantee

1. **One model for real-world and FM data.** Charts, comparisons and percentiles read only canonical tables, never raw API or FM data.
2. **Null ≠ 0, everywhere.** A stored 0 means "observed zero". A stored NULL means unknown or undefined. A missing row means "never ingested", which is a bug the tests catch.
3. **Provenance on every number.** Every stat traces to a source, competition, season, minutes and sample size, through foreign keys rather than copied columns.
4. **Idempotent and rebuildable.** Re-running a sync gives the same database. The canonical tables can be rebuilt from the raw cache without calling the API (D-004).
5. **Portable SQL.** SQLite now; nothing that blocks a move to Postgres. The one SQLite-only piece is the FTS5 search index, which is derived and can be rebuilt.

## 2. Data flow

```
PitchAPI ─► PitchApiClient ─► raw.db (raw_response) ─► parsers (Pydantic, one per endpoint)
                                                            │
                                     metric registry ──►  normalizer (absence policy per key,
                                     formation_role  ──►   role from formation + slot)
                                                            │ upsert
                                                            ▼
                     scouting.db: competition / season / team / player / match / appearance / stat_value
                                                            │ rebuild after each sync
                                                            ▼
                     aggregates: player_scope*, player_scope_stat ─► peer_group / percentile ─► API ─► UI

FM24 .rtf ─► header detection + mapping file ─► fm_import snapshot ─► same aggregate tables (its own scope)
```

## 3. Two database files

| File | Holds | Why separate |
|---|---|---|
| `raw.db` | `raw_response` only: every API response, gzip-compressed | Large (≈ 1,150 responses per league-season) and only the sync CLI needs it. It can be deleted and re-crawled, backed up separately, and never ships to a hosted site |
| `scouting.db` | Everything else | Small enough to host. It can be rebuilt from `raw.db` |

## 4. Identity

- **Internal `INTEGER` surrogate primary keys** on every entity. External IDs are never primary keys and are never parsed.
- **A source-ID map per entity type:** `competition_source_id`, `team_source_id`, `player_source_id`, `match_source_id`, each `(entity_id FK, source, external_id)` with `UNIQUE(source, external_id)`.
  - *Why one map per entity rather than one polymorphic table:* each map keeps a real foreign key, which a polymorphic `(entity_type, entity_id)` table can't.
- **`source`** values: `pitchapi`, plus `fm24` later. Each is enforced with a `CHECK` constraint.
- **Real players and FM players are separate `player` rows** (`origin = 'real' | 'fm'`). A `player_link` table connects an FM player to a real one when the user wants a cross-source comparison.
  - *Why:* an FM player is a simulated career (and a newgen has no real counterpart). Merging the two into one row would mix sources, which the project rules forbid. FM vs real comparisons go only through the explicit comparability layer.

## 5. Tables built in Phases 1–2 (real-world data)

Column types are written loosely. "nullable" is stated explicitly; everything else is `NOT NULL`.

### Reference

```
competition    id PK, name, country_code, image_url (nullable)
season         id PK, competition_id FK, label ("2025/2026" or "2026"), is_current
               UNIQUE(competition_id, label)
team           id PK, name, image_url (nullable)
player         id PK, origin ('real'|'fm'), name, short_name (nullable),
               search_name (unidecoded and lowercased, used by the search index),
               country_code (nullable), shirt_number TEXT (nullable; the profile sends a string),
               listed_slot_code (nullable; the profile's position_id, display/fallback only),
               image_url (nullable), profile_fetched_at (nullable)
*_source_id    see §4
```

### Matches and appearances

```
match          id PK, season_id FK, kickoff_utc, match_date, status,
               home_team_id FK, away_team_id FK, home_score (nullable), away_score (nullable),
               home_formation (nullable), away_formation (nullable),
               players_status   ('ok'|'missing'|'pending'),
               advanced_status  ('ok'|'unavailable'|'pending'),   -- 'unavailable' = ANALYTICS_UNAVAILABLE
               lineups_status   ('ok'|'missing'|'pending'),
               normalized_at (nullable)

appearance     id PK, match_id FK, player_id FK, team_id FK,
               is_starter (nullable; NULL when /lineups had no data),
               is_goalkeeper,              -- decides which metrics apply (registry applies_to)
               slot_code (nullable; starters' grid code, e.g. 38),
               sub_category (nullable; subs' broad category, 0–3),
               formation (nullable),
               role (nullable; detailed role, §7), side ('L'|'C'|'R', nullable),
               minutes,                    -- canonical, /players minutes_played, full match = 90 (D-010)
               minutes_actual (nullable),  -- advanced minutes incl. stoppage, for reference only
               rating (nullable),          -- rating_title; NULL = not rated (≤ 8 min, or league without ratings)
               UNIQUE(match_id, player_id)
```

- An `appearance` row exists only if the player has `minutes_played` in `/players`. Unused subs aren't stored (see §11).
- A substitute's `role` is NULL. Its `sub_category` says GK/DEF/MID/ATT.

### Metric registry and match-level values

```
metric         id PK (INTEGER), code UNIQUE (e.g. 'npxg', 'prog_passes'), label, unit,
               kind ('count'|'fraction'|'model'|'rating'|'distance'),
               source ('pitchapi_players'|'pitchapi_advanced'|'fm'),
               source_key (e.g. 'expected_goals_non_penalty' or 'passing.progressive_passes'),
               absence_policy ('zero'|'null'|'fraction_zero_attempts'),
               aggregation ('sum'|'rate_from_sums'),
               applies_to ('all'|'outfield'|'gk'),
               per90_allowed, is_model_output, higher_is_better, description

stat_value     appearance_id FK, metric_id FK,
               value REAL (nullable),        -- successes for fractions; NULL = unknown/undefined
               denominator REAL (nullable),  -- attempts, for fractions only
               PRIMARY KEY(appearance_id, metric_id)   -- WITHOUT ROWID
```

- **The registry's source of truth is a typed Python module** (`scouting.metrics.registry`), checked by mypy and tests. The `metric` table is synced from it on each run, so metric IDs stay stable.
- **Only registry metrics get ingested.** Fantasy points, the shotmap flag and other noise are dropped at parse time, but they stay in `raw.db`.
- **`applies_to`** (added in slice 1c): a metric that doesn't describe a player gets **no row**, not a 0. A striker has no `saves` row, and a keeper has no `shots` row. A player counts as a goalkeeper if he started in slot 11 or came on as a sub with category 0. Without lineup data, keeper-only stats like `saves` decide it.
- **`absence_policy`** values are `zero`, `null` and `fraction_zero_attempts`. "Not rated" applies only to `appearance.rating`, which isn't a registry metric.
- **No `stat_value` row** therefore means one of two things: the metric doesn't apply to the player, or its source had no data for the match (e.g. `ANALYTICS_UNAVAILABLE`). Either way, those minutes don't count as exposure for that metric.

### Positions

```
formation_role formation, slot_code, role, side, PRIMARY KEY(formation, slot_code)
```

Seeded from code (§7). A (formation, slot) pair that isn't in the table gets `role = NULL`, and the sync logs a warning. It never guesses.

### Sync state

```
sync_state     season_id PK FK, match_list_fetched_at (nullable), last_full_sync_at (nullable)
```

`match_list_fetched_at` is the `fetched_at` of the match-list response that was used, not the wall-clock time of the sync, so re-running a sync from cache leaves the database identical (renamed from `match_list_synced_at` in slice 1b). Timestamps are stored as naive UTC. The three `match.*_status` columns also have database-side defaults of `'pending'`.

Per-match progress comes from `raw_response` plus the `match.*_status` columns, so a crawl that was killed resumes by fetching whatever is still `pending`.

### Search

```
player_fts     FTS5 virtual table over (search_name, short_name), content='player'
```

Derived and rebuilt after each sync. rapidfuzz then re-ranks the FTS candidates, which handles typos and forms like "E. Haaland".

### Raw cache (`raw.db`)

```
raw_response   cache_key PK (method + path + sorted query params), path, params_json,
               status, error_code (nullable), error_message (nullable),
               body_gz BLOB (nullable; gzip of the `data` payload),
               fetched_at
```

- **Freshness is worked out when a response is read:** `fetched_at` plus the current `FreshnessPolicy` for that endpoint class. There's no stored `expires_at`/`recheck_at` (changed in slice 1a, 2026-11-19), so changing a TTL also applies to responses already stored. For example, a match list fetched while its season was current stops being refetched once the season is complete.
- **Managed with stdlib `sqlite3`, not Alembic.** It's one table. The schema version lives in `PRAGMA user_version`, and opening a file with a different version fails.

## 6. Aggregates (Phase 2)

Aggregates are keyed on a **scope**, so real seasons and FM snapshots share one shape. That's what lets the comparison and chart code serve both modes unchanged.

```
scope              id PK, kind ('season'|'fm_import'),
                   season_id FK (nullable), fm_import_id FK (nullable),
                   CHECK (exactly one of season_id / fm_import_id is set)

player_scope       player_id, scope_id, minutes, matches, starts,
                   primary_role (nullable), peer_role (nullable),
                   avg_rating (nullable), rated_matches, computed_at,
                   PRIMARY KEY(player_id, scope_id)
player_scope_team  player_id, scope_id, team_id, minutes        -- mid-season moves inside one competition
player_scope_role  player_id, scope_id, role, minutes           -- shows how primary_role was chosen

player_scope_stat  player_id, scope_id, metric_id,
                   total (nullable), denominator (nullable),
                   minutes,                 -- exposure: minutes in matches where this metric's source had data
                   matches_with_value,
                   per90 (nullable), rate (nullable),
                   PRIMARY KEY(player_id, scope_id, metric_id)

peer_group         id PK, scope_id, peer_role, min_minutes, size, computed_at,
                   UNIQUE(scope_id, peer_role, min_minutes)
percentile         peer_group_id, player_id, metric_id, basis ('per90'|'rate'|'total'),
                   value, pct, PRIMARY KEY(peer_group_id, player_id, metric_id)
```

### Aggregation rules

- **Exposure minutes are counted per metric.** If a match was `ANALYTICS_UNAVAILABLE`, its minutes don't count toward advanced metrics. Otherwise unrated matches would quietly push down xT/VAEP per 90.
- **Per 90:** `per90 = total / minutes × 90`, using the canonical `/players` minutes (D-010). It's NULL when `minutes = 0`.
- **Fractions:** `rate = SUM(value) / SUM(denominator)`, NULL when the denominator is 0. **Never average per-match percentages.**
- **Rating:** `avg_rating` is the simple mean over rated matches, and `rated_matches` is always shown next to it. It's never per-90'd, and unrated matches are excluded rather than counted as 0. Low priority: it's stored and shown on the player card, but it isn't part of any default radar template.
- **Percentiles** are computed within an explicit peer group: same scope (competition-season), same `peer_role`, `minutes ≥ min_minutes`. The UI always shows the group and its size. FM percentiles are computed only within their own import's population.
- **Minimum minutes:** the default is 25% of the scope's possible minutes (≈ 855 for a 38-match season), scaled to matches played for a season in progress, and **adjustable in the UI**. A non-default threshold is computed on the fly from `player_scope_stat`; only the default is stored in `percentile`.
- **Players added manually:** you can add any player to a comparison, even one below the threshold or from another peer role. That player is ranked against the chosen peer group's distribution but **isn't added to it**, so the other players' percentiles don't change. The UI flags them ("below minimum minutes" / "outside peer group") and shows their minutes.
- **When aggregates run:** they're rebuilt for each affected scope after a sync, so the UI never scans `stat_value`.

## 7. Positions: from formation + slot to role

- **Detailed roles:** `GK, CB, FB, WB, DM, CM, AM, W, ST`, plus a side (`L`/`C`/`R`) from the slot column. Low columns are the right flank (spike-verified).
- **The slot alone doesn't decide the role.** Slots 84/86 are 10s in a 3-4-2-1, but in a 4-1-4-1 or 4-1-2-3 they're central midfielders (8s) in front of a single holding midfielder. So `formation_role` is an **explicit mapping per formation**, written by hand and reviewed, not generated by a rule. Mapping for the 8 formations seen in the spike (`row = slot // 10`; columns 2/8 etc. are flanks, low = right):

  | Formation | GK | Defence | Midfield | Attack |
  |---|---|---|---|---|
  | 4-2-3-1 | 11 | 32/38 FB, 34/36 CB | 64/66 DM | 83/87 W, 85 AM, 115 ST |
  | 4-3-3 | 11 | 32/38 FB, 34/36 CB | 73/75/77 CM | 103/107 W, 105 ST |
  | 4-4-2 | 11 | 32/38 FB, 34/36 CB | 72/78 W, 74/76 CM | 104/106 ST |
  | 4-1-4-1 | 11 | 32/38 FB, 34/36 CB | 65 DM; 84/86 CM; 82/88 W | 115 ST |
  | 4-1-2-3 | 11 | 32/38 FB, 34/36 CB | 65 DM; 84/86 CM | 103/107 W, 105 ST |
  | 3-4-2-1 | 11 | 33/35/37 CB | 62/68 WB, 64/66 CM | 84/86 AM, 105 ST |
  | 3-4-3 | 11 | 33/35/37 CB | 72/78 WB, 74/76 CM | 103/107 W, 105 ST |
  | 5-3-2 | 11 | 33/35/37 CB, 51/59 WB | 73/75/77 CM | 104/106 ST |

  Judgement calls to check against real players in Phase 2: 4-2-3-1's 64/66 as DM rather than CM (it doesn't change the peer group, since DM and CM share one), and 3-4-2-1's 84/86 as AM rather than W.
- **Formations not in the table:** the sync logs them, and their starters get `role = NULL` (the appearance still counts, through the broad category) until the mapping is added. It never guesses. The Phase 1 crawl lists every formation PitchAPI actually uses, so the table can be completed before Phase 2.
- **Tests:** a table-driven test covers every (formation, slot) pair, plus known players (Frimpong 32 → FB/R, Kerkez 38 → FB/L, Wirtz 85 → AM).
- **A player's season role:** `primary_role` is the role with the most minutes **as a starter**.
  - Fallback 1: the profile's `listed_slot_code` (formation-free rules).
  - Fallback 2: the most common `sub_category` (broad only).
- **Peer roles** (7): the position presets from the requirements, plus a separate group for central 10s (D-011):

  | `peer_role` | Detailed roles it groups | Note |
  |---|---|---|
  | GK | GK | |
  | CB | CB | |
  | FB | FB, WB | |
  | CM | DM, CM | |
  | AM | AM (central 10s: 85 in 4-2-3-1; 84/86 in 3-4-2-1) | A 10 isn't judged against deeper CMs, e.g. Nico Paz vs Barrios |
  | W | W (wingers, wide AMs 83/87, wide midfielders in a 4-4-2 / 4-1-4-1) | |
  | ST | ST | |

  The AM group is the smallest (only teams that play a 10), so expect small-sample warnings more often.

## 8. FM tables (Phase 6, sketch only: no migration until then)

```
fm_import      id PK, label, file_name, file_sha256 UNIQUE, game_version ('FM24'),
               in_game_date (nullable), imported_at, columns_json
fm_attribute   fm_import_id FK, player_id FK, attribute, value_low, value_high   -- ranges for partly scouted players
player rows    origin = 'fm', with player_source_id(source = 'fm24', external_id = FM unique ID) when the export has it
FM stats       metric rows with source = 'fm'; written straight into player_scope_stat under scope(kind = 'fm_import'),
               because FM exports are already season totals
metric_mapping fm_metric_id, real_metric_id, comparability ('direct'|'approximate'|'not_comparable'), note   -- Phase 7
player_link    fm_player_id, real_player_id, method ('manual'|'name_dob'|'name_team'), confidence, created_at
```

Each import is a snapshot, so tracking a player over time means comparing scopes. Nothing here depends on the FM format beyond "a table of columns". The real header mapping waits for a real `.rtf` file (D-006).

## 9. Normalization and idempotency

- **Upserts go through natural keys:** the source-ID maps, `appearance UNIQUE(match_id, player_id)` and `stat_value`'s primary key.
- **Normalizing one match is a single transaction.** It upserts the match, upserts its appearances, then deletes and re-inserts their `stat_value` rows. That's simpler than diffing, and re-running it gives identical rows.
- **Absence policy is applied here, from the registry** (PITCHAPI_NOTES, "What a missing key or null means"):
  - `zero` → store 0
  - `null` → store NULL
  - `fraction_zero_attempts` → value 0, denominator 0
  - `not_rated` → `appearance.rating = NULL`
- **Parse checks:**
  - a key that appears in two groups with conflicting values → fail loudly
  - an unknown `stat.type` → fail loudly
  - an unknown (formation, slot) → log a warning, and `role = NULL`
  - an unknown raw key → log it once per sync (new provider stats are noticed but not ingested)

## 10. Rough size

- **Per season:** an EPL season is about 380 matches × ~31 appearances ≈ 11.8k appearances. At about 80 registry metrics that's ≈ 0.95M `stat_value` rows, roughly 25 MB with `WITHOUT ROWID` and integer metric IDs.
- **Top 5 × 6 seasons:** ≈ 28M rows, about 0.7 GB. That's fine for SQLite. The UI only reads aggregates, which are smaller by a factor of ~30 per season.
- **`raw.db`:** about 1,150 responses per league-season, gzip-compressed. Measure the size in Phase 1.

## 11. Resolved questions (2026-11-19, D-011)

| # | Question | Answer |
|---|---|---|
| 1 | Long `stat_value` table, or wide columns? | **Long** |
| 2 | Separate `raw.db` with gzip bodies? | **Yes** |
| 3 | Peer roles | **7 groups**: central 10s (AM) are their own group; wide AMs and wide midfielders go with wingers (§7) |
| 4 | Rating average | **Simple mean over rated matches.** Low priority, not in the default radars |
| 5 | Minimum minutes | **25% of possible minutes by default, adjustable in the UI**, and players can be added to a comparison manually (§6) |
| 6 | Unused substitutes | **Not stored** |
| 7 | Mid-season moves within one competition | **One row per player per competition-season**, with a per-team breakdown |
| 8 | FM unique ID in `.rtf` exports | **Verify in Phase 6** with a real file |

## 12. What Phase 1 builds from this

- **Alembic migrations:**
  - **Reference:** `competition`, `season`, `team`, `player`, and the four `*_source_id` maps
  - **Matches and stats:** `match`, `appearance`, `metric`, `stat_value`, `formation_role`
  - **Sync and search:** `sync_state`, `player_fts`
  - (`raw.db`'s `raw_response` is created by `RawStore`, not Alembic; done in slice 1a)
- **Code:** the cache layer over `PitchApiClient`, Pydantic parsers for the 5 endpoints, the registry (an initial metric list), the normalizer, and the `sync` CLI command.
- **Exit test:** two full EPL 2025/26 syncs produce an identical `scouting.db`, and a sync killed halfway resumes correctly.
- §6 (aggregates and percentiles) is Phase 2. §8 (FM) is Phases 6–7.