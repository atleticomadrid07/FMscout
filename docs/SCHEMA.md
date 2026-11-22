# SCHEMA.md: Canonical data model (v1.1, approved)

**Status:** APPROVED 2026-11-19 (D-011), with the answers to §11 folded in. Approving it closed Phase 0. Further changes go through DECISIONS.md (latest: D-018, slice 2d-3).
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
                                     formation_role  ──►   role from formation + slot,
                                                           depth from the heatmap)
                                                            │ upsert
                                                            ▼
                     scouting.db: competition / season / team / player / match / appearance / stat_value
                                                            │ rebuild after each sync
                                                            ▼
                     aggregates: player_scope* (incl. the 6/8 split), player_scope_stat ─► peer_group / percentile ─► API ─► UI

FM24 .rtf ─► header detection + mapping file ─► fm_import snapshot ─► same aggregate tables (its own scope)
```

## 3. Two database files

| File | Holds | Why separate |
|---|---|---|
| `raw.db` | `raw_response` only: every API response, gzip-compressed | Large (≈ 1,520 responses per league-season since heatmaps) and only the sync CLI needs it. It can be deleted and re-crawled, backed up separately, and never ships to a hosted site |
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
               heatmaps_status  ('ok'|'missing'|'pending'),       -- slice 2d; no CHECK (see below)
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
               depth (nullable),           -- slice 2d: heatmap depth, 0 = own goal line .. 1 = opponent's
               depth_actions (nullable),   -- slice 2d: the actions that depth rests on
               UNIQUE(match_id, player_id)
```

- An `appearance` row exists only if the player has `minutes_played` in `/players`. Unused subs aren't stored (see §11).
- A substitute's `role` is NULL. Its `sub_category` says GK/DEF/MID/ATT.
- **Depth (slice 2d-1, D-017):** the actions-weighted mean of the player's heatmap cell centres along the pitch, divided by the grid length, in the `acting_ltr` frame (every team attacks towards high x). Subs get one too. NULL when the match's heatmaps aren't fetched (`pending`) or `missing`, when the player isn't on the heatmap, or when he had no actions (`depth_actions = 0`). It isn't a registry metric: it says where a player plays, and gets no percentiles.
- **`heatmaps_status` has no CHECK constraint:** adding one would rebuild `match`, which `appearance` references (ERRORS.md). Its values are the same three as `players_status`, set only by the sync.

### Metric registry and match-level values

```
metric         id PK (INTEGER), code UNIQUE (e.g. 'npxg', 'prog_passes'), label, unit,
               kind ('count'|'fraction'|'model'|'rating'|'distance'),
               source ('pitchapi_players'|'pitchapi_advanced'|'fm'),
               source_key (e.g. 'expected_goals_non_penalty' or 'passing.progressive_passes'),
               absence_policy ('zero'|'null'|'fraction_zero_attempts'),
               aggregation ('sum'|'rate_from_sums'),
               applies_to ('all'|'outfield'|'gk'),
               per90_allowed, is_model_output, higher_is_better, description,
               denominator_key (nullable),  -- advanced two-field fractions and keeper-rate weights
               value_is_rate (default false), -- value = per-match rate × weight (slice 1d)
               min_attempts (nullable),       -- fractions: fewest season attempts for a percentile (2b)
               direction ('higher'|'lower'|'none', default 'higher')  -- for UI colouring (2b)

stat_value     appearance_id FK, metric_id FK,
               value REAL (nullable),        -- successes for fractions; NULL = unknown/undefined
               denominator REAL (nullable),  -- attempts, for fractions only
               PRIMARY KEY(appearance_id, metric_id)   -- WITHOUT ROWID
```

- **The registry's source of truth is a typed Python module** (`scouting.metrics.registry`), checked by mypy and tests. The `metric` table is synced from it on each run, so metric IDs stay stable.
- **Only registry metrics get ingested.** Fantasy points, the shotmap flag and other noise are dropped at parse time, but they stay in `raw.db`.
- **`applies_to`** (added in slice 1c): a metric that doesn't describe a player gets **no row**, not a 0. A striker has no `saves` row, and a keeper has no `shots` row. A player counts as a goalkeeper if he started in slot 11 or came on as a sub with category 0. Without lineup data, keeper-only stats like `saves` decide it.
- **`absence_policy`** values are `zero`, `null` and `fraction_zero_attempts`. "Not rated" applies only to `appearance.rating`, which isn't a registry metric.
- **Two-field fractions** (slice 1d): the advanced endpoint sends some fractions as two counts (e.g. `carrying.take_ons_won` of `carrying.take_ons`). `source_key` is the successes and `denominator_key` the attempts. If the attempts field is missing, the absence policy applies.
- **Per-match rates** (`value_is_rate`, slice 1d): the keeper rates (`distribution_accuracy`, `launch_pct`, `claim_rate`, `avg_pass_length`) are stored as `value = rate × weight`, `denominator = weight` (distributions or claims), on the scale PitchAPI sends (percentages stay 0–100). A season then gets the exact weighted average through `rate_from_sums`. A weight of 0 stores (0, 0); a NULL rate with a non-zero weight stores (NULL, NULL). These are never per-90'd. (`launch_pct` and `claim_rate` turned out to be their counts' ratios, rounded to 0.1: D-016.)
- **A whole missing group** (e.g. `goalkeeping` is null for a substitute keeper) gives **no rows** for that group's metrics, not zeros.
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

`match_list_fetched_at` is the `fetched_at` of the match-list response that was used, not the wall-clock time of the sync, so re-running a sync from cache leaves the database identical (renamed from `match_list_synced_at` in slice 1b). Timestamps are stored as naive UTC. The four `match.*_status` columns also have database-side defaults of `'pending'`.

Per-match progress comes from `raw_response` plus the `match.*_status` columns, so a crawl that was killed resumes by fetching whatever is still `pending`.

**Season crawl (`scouting sync season`, slice 1d; heatmaps since 2d-1):**
- Syncs the match list, then processes every match with any status other than `ok`, in kickoff order. Each match is 4 requests: players, advanced, lineups, heatmaps. `unavailable`/`missing` matches are looked at again each run, but the negative cache means no request until the re-check date.
- **Heatmaps never fetched + offline run = `pending`, not an error,** so `--offline --renormalize` works on a raw.db from before slice 2d. The next online run fetches only the missing heatmaps (the other responses come from cache). Pending heatmaps don't count as "still to sync", so aggregates still rebuild.
- **One transaction per match.** A kill or Ctrl-C loses at most the match in progress, which is still `pending` next run. A match that fails to parse is reported and left `pending`; the season continues.
- `--limit N` processes N matches and stops. `--renormalize` re-processes every match (use with `--offline` after a registry or role-mapping change; nothing is fetched).
- Then it fetches profiles for the season's players (skipped after Ctrl-C), committing every 25 players with progress output. A Ctrl-C there keeps the finished chunks; a profile with an unexpected shape is reported and skipped.
- `last_full_sync_at` = the latest `match.normalized_at` once nothing is pending. `normalized_at` is the newest `fetched_at` among the responses used (heatmaps included). Deterministic, like every other timestamp.
- **Profiles are the authority** for `name`, `short_name`, `country_code`, `shirt_number` and `listed_slot_code`; the per-match payloads only create the player.
- `scouting report checksum` hashes every canonical table (sorted rows), so "two runs give an identical DB" is checked with one command. `scouting report formations` lists every formation in a season and any starter slot without a role.

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

- **Freshness is worked out when a response is read:** `fetched_at` plus the current `FreshnessPolicy` for that endpoint class. There's no stored `expires_at`/`recheck_at` (changed in slice 1a, 2026-11-19), so changing a TTL also applies to responses already stored. For example, a match list fetched while its season was current stops being refetched once the season is complete. Heatmaps use the finished-match policy (never refetched; a 404 is re-checked weekly).
- **Managed with stdlib `sqlite3`, not Alembic.** It's one table. The schema version lives in `PRAGMA user_version`, and opening a file with a different version fails.

## 6. Aggregates (Phase 2; built in slices 2a, 2b and 2d-3)

Aggregates are keyed on a **scope**, so real seasons and FM snapshots share one shape. That's what lets the comparison and chart code serve both modes unchanged.

```
scope              id PK, kind ('season'|'fm_import'),
                   season_id FK (nullable), fm_import_id FK (nullable),
                   CHECK (exactly one of season_id / fm_import_id is set)

player_scope       player_id, scope_id, minutes, matches, starts,
                   primary_role (nullable), peer_role (nullable; GK CB FB DM CM AM W ST),
                   role_source ('starts'|'listed', nullable),   -- slice 2a
                   avg_rating (nullable), rated_matches, computed_at,
                   -- slice 2d-3 (D-018), central mids only, NULL for everyone else:
                   peer_method ('depth'|'partner'|'formation', nullable),
                   depth (nullable),           -- share of pitch length in front of his CBs (× 105 = m)
                   depth_starts (nullable),    -- central-mid starts with a heatmap behind depth
                   partner_gap (nullable),     -- + = his deepest fellow central mid played deeper
                   partner_starts (nullable),  -- starts beside another central mid
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
                   UNIQUE(scope_id, peer_role, min_minutes)   -- upserted, so IDs stay stable (2b)
percentile         peer_group_id, player_id, metric_id, basis ('per90'|'rate'),
                   value, pct, ranked_among,   -- eligible players for this metric in the group
                   PRIMARY KEY(peer_group_id, player_id, metric_id)   -- WITHOUT ROWID
```

**The 2d-3 migration** dropped and recreated `percentile`, `peer_group` and `player_scope` (adding DM to the `peer_role` CHECKs rebuilds a table on SQLite, which fails while it's referenced; as derived tables they're refilled by `scouting aggregate`). The downgrade restores the 7-role CHECK and drops the five columns the same way.

### Aggregation rules

- **Exposure minutes are counted per metric:** the minutes of the appearances that have a `stat_value` row for it. If a match was `ANALYTICS_UNAVAILABLE`, its minutes don't count toward advanced metrics. Otherwise unrated matches would quietly push down xT/VAEP per 90.
- **A stored NULL** follows the registry's `season_null` (slice 2a, D-014): `skip` = unknown, adds neither total nor minutes (the default); `zero` = a known zero, adds 0 and its minutes (`xgot`: NULL means no shot on target; `was_fouled`: PitchAPI sends NULL, never 0, for a player who wasn't fouled, D-016).
- **Primary role** (slice 2a): most minutes as a starter (ties: more starts, then role order). No starts: the profile's listed slot, only where every mapped formation agrees on it (`role_source = 'listed'`; e.g. 11 → GK, but 84 → none because it's W, AM or CM depending on the formation).
- **The 6/8 split (slice 2d-3, D-018):** every player whose formation peer role is DM or CM (any minutes, so a player added to a comparison by hand has a group too) is re-decided before percentiles:
  - **≥ 5 central-mid starts with a heatmap → depth decides:** season depth = minutes-weighted mean, over his DM/CM-role starts, of his depth minus the mean depth of his team's starting centre-backs. **≥ 14 m (0.1333) → CM (8)**; below → **DM (6)**, unless his deepest fellow starting central mid played on average **≥ 2 m deeper** over **≥ 5 starts** beside one → **CM, method `partner`**.
  - **Otherwise the formation:** DM if more than half of his central-mid minutes came as the only DM-role starter in his side (a single pivot), else CM (a 4-2-3-1 double pivot mixes 6s and 8s). A listed-slot player whose slot is DM in some formations and CM in others gets CM.
  - Shown as "borderline" within 1 m of the cut. The outfield-average depth is a second opinion in `report depth` only, not stored.
- **Rebuilds are deterministic:** one transaction per scope (delete, then insert); `computed_at` is the latest `match.normalized_at`; the `scope` row is kept, so its ID is stable.
- **Per 90:** `per90 = total / minutes × 90`, using the canonical `/players` minutes (D-010). It's NULL when `minutes = 0`.
- **Fractions:** `rate = SUM(value) / SUM(denominator)`, NULL when the denominator is 0. **Never average per-match percentages.**
- **Rating:** `avg_rating` is the simple mean over rated matches, and `rated_matches` is always shown next to it. It's never per-90'd, and unrated matches are excluded rather than counted as 0. Low priority: it's stored and shown on the player card, but it isn't part of any default radar template.
- **Percentiles** are computed within an explicit peer group: same scope (competition-season), same `peer_role`, `minutes ≥ min_minutes`. The UI always shows the group and its size. FM percentiles are computed only within their own import's population.
- **Percentile formula (D-014):** midpoint, pct = (below + ½ × tied) ÷ n × 100, on per-90 for counts, model outputs and distances and on rate for fractions. Stored raw (low value = low percentile); the UI flips lower-is-better metrics. Eligibility is per metric: per-90 metrics need the minimum minutes on that metric's own exposure, and fraction metrics need `metric.min_attempts`; an ineligible player has no `percentile` row for that metric (the UI derives the reason from `player_scope_stat`). `ranked_among` records how many were ranked. Groups under 20 players get a small-sample warning. A player outside the group (added by hand) is ranked on the fly against the stored values without joining it (`rank_against`).
- **Minimum minutes:** the default is 25% of the scope's possible minutes (≈ 855 for a 38-match season), scaled to matches played for a season in progress, and **adjustable in the UI**. A non-default threshold is computed on the fly from `player_scope_stat`; only the default is stored in `percentile`.
- **Players added manually:** you can add any player to a comparison, even one below the threshold or from another peer role. That player is ranked against the chosen peer group's distribution but **isn't added to it**, so the other players' percentiles don't change. The UI flags them ("below minimum minutes" / "outside peer group") and shows their minutes.
- **When aggregates run:** they're rebuilt for each affected scope after a sync, so the UI never scans `stat_value`.

## 7. Positions: from formation + slot to role

- **Detailed roles:** `GK, CB, FB, WB, DM, CM, AM, W, ST`, plus a side (`L`/`C`/`R`) from the slot column. Low columns are the right flank (spike-verified).
- **The slot alone doesn't decide the role.** Slots 84/86 are 10s in a 4-3-2-1, wide forwards in a 3-4-2-1, and central midfielders (8s) in front of a single holding midfielder in a 4-1-4-1 or 4-1-2-3. So `formation_role` is an **explicit mapping per formation**, written by hand and reviewed, not generated by a rule. Mapping for the 19 formations seen so far (`row = slot // 10`; columns 2/8 etc. are flanks, low = right):

  | Formation | GK | Defence | Midfield | Attack |
  |---|---|---|---|---|
  | 4-2-3-1 | 11 | 32/38 FB, 34/36 CB | 64/66 DM | 83/87 W, 85 AM, 115 ST |
  | 4-3-3 | 11 | 32/38 FB, 34/36 CB | 73/75/77 CM | 103/107 W, 105 ST |
  | 4-4-2 | 11 | 32/38 FB, 34/36 CB | 72/78 W, 74/76 CM | 104/106 ST |
  | 4-1-4-1 | 11 | 32/38 FB, 34/36 CB | 65 DM; 84/86 CM; 82/88 W | 115 ST |
  | 4-1-2-3 | 11 | 32/38 FB, 34/36 CB | 65 DM; 84/86 CM | 103/107 W, 105 ST |
  | 3-4-2-1 | 11 | 33/35/37 CB | 62/68 WB, 64/66 CM | 84/86 W (D-016), 105 ST |
  | 3-4-3 | 11 | 33/35/37 CB | 72/78 WB, 74/76 CM | 103/107 W, 105 ST |
  | 5-3-2 | 11 | 33/35/37 CB, 51/59 WB | 73/75/77 CM | 104/106 ST |
  | 3-5-2 | 11 | 33/35/37 CB | 71/79 WB, 73/75/77 CM | 104/106 ST |
  | 5-4-1 | 11 | 33/35/37 CB, 51/59 WB | 72/78 W, 74/76 CM | 115 ST |
  | 4-4-1-1 | 11 | 32/38 FB, 34/36 CB | 72/78 W, 74/76 CM | 95 AM, 115 ST |
  | 3-4-1-2 | 11 | 33/35/37 CB | 62/68 WB, 64/66 CM | 85 AM, 104/106 ST |
  | 4-1-3-2 | 11 | 32/38 FB, 34/36 CB | 65 DM; 83/87 CM | 85 AM, 104/106 ST |
  | 4-3-2-1 | 11 | 32/38 FB, 34/36 CB | 63/65/67 CM | 84/86 AM, 115 ST |
  | 4-5-1 | 11 | 32/38 FB, 34/36 CB | 71/79 W, 73/75/77 CM | 115 ST |
  | 4-2-2-2 | 11 | 32/38 FB, 34/36 CB | 64/66 DM | 82/88 W, 104/106 ST |
  | 4-3-1-2 | 11 | 32/38 FB, 34/36 CB | 63/65/67 CM | 85 AM, 104/106 ST |
  | 3-5-1-1 | 11 | 33/35/37 CB | 71/79 WB, 73/75/77 CM | 95 AM, 115 ST |
  | 5-3-1-1 | 11 | 33/35/37 CB, 51/59 WB | 73/75/77 CM | 95 AM, 115 ST |

  The first 8 rows came from the spike; the other 11 from `report formations` after the EPL 2025/26 crawl, which covers every formation used that season (D-013).

  **Judgement calls, checked against real players in slice 2c (D-016):** 3-4-2-1's 84/86 changed from AM to **W** (mostly wingers playing as inside forwards); 4-3-2-1's 84/86 stay AM; 4-2-3-1's 64/66 stay DM; slot 95 stays AM. The 4-2-3-1 double pivot mixes 6s and 8s, which formation slots can't separate: the reason for the 6/8 split by heatmap depth (§6).
- **Formations not in the table:** the sync reports them in its summary, and their starters get `role = NULL` (the appearance still counts, through the broad category) until the mapping is added. It never guesses. `scouting report formations` lists them for a season; new leagues will add more.
- **Tests:** a table-driven test covers every (formation, slot) pair, plus known players (Frimpong 32 → FB/R, Kerkez 38 → FB/L, Wirtz 85 → AM).
- **A player's season role:** `primary_role` is the role with the most minutes **as a starter**.
  - Fallback (no starts): the profile's `listed_slot_code`, only where every mapped formation agrees on that slot (§6, slice 2a).
  - Otherwise no role. A broad `sub_category` alone can't pick a peer group (DEF could be CB or FB), so it isn't used; such players are almost always below the minimum minutes anyway (EPL 2025/26: 44 without a role, none above 855 minutes).
- **Peer roles** (8 since slice 2d-3): the position presets from the requirements, plus separate groups for 6s and central 10s (D-011, D-018):

  | `peer_role` | Detailed roles it groups | Note |
  |---|---|---|
  | GK | GK | |
  | CB | CB | |
  | FB | FB, WB | |
  | DM | DM, CM (6s) | Decided by season heatmap depth, not by slot (§6). EPL 2025/26: 28 |
  | CM | DM, CM (8s) | Same rule. EPL 2025/26: 48 (incl. Bernardo and João Gomes by the partner rule, Bergvall by formation) |
  | AM | AM (central 10s: 85 in 4-2-3-1; 84/86 in 4-3-2-1; 95) | A 10 isn't judged against deeper CMs, e.g. Nico Paz vs Barrios. EPL 2025/26: 17 players, below the warning line, accepted (D-017) |
  | W | W (wingers, wide AMs 83/87, wide midfielders in a 4-4-2 / 4-1-4-1, 3-4-2-1's 84/86) | |
  | ST | ST | |

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

Each import is a snapshot, so tracking a player over time means comparing scopes. Nothing here depends on the FM format beyond "a table of columns". The real header mapping waits for a real `.rtf` file (D-006). FM scopes have no heatmaps, so the 6/8 split for FM players will need its own rule (FM's position/role), decided in Phase 6.

## 9. Normalization and idempotency

- **Upserts go through natural keys:** the source-ID maps, `appearance UNIQUE(match_id, player_id)` and `stat_value`'s primary key.
- **Normalizing one match is a single transaction.** It upserts the match, upserts its appearances (depth included), then deletes and re-inserts their `stat_value` rows. That's simpler than diffing, and re-running it gives identical rows.
- **Absence policy is applied here, from the registry** (PITCHAPI_NOTES, "What a missing key or null means"):
  - `zero` → store 0
  - `null` → store NULL
  - `fraction_zero_attempts` → value 0, denominator 0
  - `not_rated` → `appearance.rating = NULL`
- **Parse checks:**
  - a key that appears in two groups with conflicting values → fail loudly
  - an unknown `stat.type` → fail loudly
  - heatmaps in another frame than `acting_ltr`, a cell outside the grid, a negative count, a player listed twice, or heatmaps for another match → fail loudly
  - an unknown (formation, slot) → log a warning, and `role = NULL`
  - an unknown raw key → log it once per sync (new provider stats are noticed but not ingested)

## 10. Rough size

- **Per season:** an EPL season is about 380 matches × ~31 appearances ≈ 11.8k appearances. At about 80 registry metrics (90 defined after slice 1d; a player gets only the ones that apply) that's ≈ 0.95M `stat_value` rows, roughly 25 MB with `WITHOUT ROWID` and integer metric IDs.
- **Top 5 × 6 seasons:** ≈ 28M rows, about 0.7 GB. That's fine for SQLite. The UI only reads aggregates, which are smaller by a factor of ~30 per season.
- **`raw.db`:** about 1,520 responses per league-season, gzip-compressed (heatmaps add 380).

## 11. Resolved questions (2026-11-19, D-011)

| # | Question | Answer |
|---|---|---|
| 1 | Long `stat_value` table, or wide columns? | **Long** |
| 2 | Separate `raw.db` with gzip bodies? | **Yes** |
| 3 | Peer roles | **7 groups** at first: central 10s (AM) are their own group; wide AMs and wide midfielders go with wingers (§7). **8 since slice 2d-3** (DM split from CM by heatmap depth, D-018) |
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