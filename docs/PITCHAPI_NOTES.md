# PITCHAPI_NOTES.md

Verified facts about PitchAPI that this project depends on. Written in our own words; the official docs are the authority, **except where the spike found that real responses differ (marked ⚠️)**.

**Sources:**
- The official docs (PDF export, changelog current to Nov 14, 2026)
- The source of the Python SDK `pitchapi` 0.1.6 (MIT license)
- Live responses from the Phase 0 spike, 2026-11-19 (marked "spike")
- The full EPL 2025/26 crawl, 2026-11-20 (marked "crawl"; see its section below)

**Spike sample:** 9 matches. Five from EPL 2025/26 (`m_2RfPIg`, `m_0kLiyr`, `m_0is5BY`, `m_1t3Zja`, `m_1gBZfF`), plus the most recent 2026/27 match from each of Serie A (`m_1TtlDk`), LaLiga (`m_1FwCGv`), Bundesliga (`m_1yCXSs`) and Ligue 1 (`m_3xBldf`).

**Last checked:** 2026-11-20

## Basics

| | |
|---|---|
| Base URL | `https://api.pitchapi.dev`. All paths are under `/v1`. Plain HTTP is refused, not redirected |
| Auth | `X-API-KEY` header. Keys start with `pk_live_` or `pk_test_` |
| SDK key variable | `PITCHAPI_API_KEY`, the same name as our `.env.example` |
| Envelope | `{"data": …}` on success or `{"error": {"code", "message"}}` on failure, never both. Branch on `code` |
| Limits | No per-second or per-day allowance. There is only an unpublished burst guard, which returns 429 `RATE_LIMIT_EXCEEDED` with `Retry-After`. Spike: about 45 requests at 1 request/s, no 429s |
| Coverage | 77 leagues (spike: confirmed), history back to 2021 |
| IDs | Prefix + `_` + 6 base62 characters (`m_`, `p_`, `t_`, `l_`, `s_`). Opaque and stable. Shot IDs are unique only within their match |
| Wrapping (spike) | Inconsistent between endpoints. `/leagues` → `{"leagues": [...]}`; match list → `{"league": {...}, "matches": [...]}`; `/advanced/players` → `{"players": [...]}`; but `/matches/{id}/players` → **a bare list**. Model each endpoint's shape explicitly |

## Endpoints we plan to use

| Endpoint | Used for | Phase |
|---|---|---|
| `GET /v1/leagues` | League IDs, and the seasons available for each league | 1 |
| `GET /v1/leagues/{id}/matches?season=&status=` | Match list for a season, including team IDs and names | 1 |
| `GET /v1/matches/{id}/players` | Per-match player stat lines, including the match rating | 1 |
| `GET /v1/matches/{id}/advanced/players` | xT, VAEP, progressive passes and carries, SCA/GCA, xAG, defending, goalkeeping | 1 |
| `GET /v1/matches/{id}/lineups` | Per-match position (see below) | 1 |
| `GET /v1/players/{id}` | Profile: full name, short name, shirt number, `position_id`, country, image | 1 |
| `GET /v1/teams/{id}` | Team image/details. Probably unnecessary: the match list already carries team `id`, `name` and `image_url` | 1 (maybe) |
| `GET /v1/matches/{id}/shots`, `/heatmaps`, `/advanced/network` | Rich visuals | 8 |
| `GET /v1/matches/{id}/advanced`, `/stats` | Team pages | 5 |

**Not used:**
- `/live`. It exists (added Nov 14, 2026, limited to 60 requests/minute), but we work from post-match data only (D-005).
- `/players/halves`.

**Crawl cost per season (spike-based):** 1 match-list request, plus 3 per match (players, advanced, lineups), plus first-time player profiles. That's about 1,141 + profiles for an EPL season.

## Leagues (`/v1/leagues`), spike

- **Shape:** `data` is `{"leagues": [...]}`.
- **Fields per league:** `id`, `name`, `country_code`, `image_url`, `is_free`, `seasons`.
- **Names aren't unique.** Examples: "Premier League" exists for ENG, RUS, EGY and CAN; "Serie A" for ITA, BRA and ECU; "Bundesliga" for GER and AUT. Always key on `id`, and display `country_code` next to the name.
- **`country_code` isn't always a country:** international competitions use `INT` or `INT-2`.
- **Season coverage varies by league.** Many leagues only go back to 2024 or 2025. All of the top 5 go back to 2021/2022.

### Top-5 league IDs

| League | `country_code` | `id` | Seasons |
|---|---|---|---|
| Premier League | ENG | `l_4WFCIZ` | 2021/2022 – 2026/2027 |
| LaLiga | ESP | `l_0ErfuF` | 2021/2022 – 2026/2027 |
| Bundesliga | GER | `l_1Isor4` | 2021/2022 – 2026/2027 |
| Serie A | ITA | `l_0ALvwF` | 2021/2022 – 2026/2027 |
| Ligue 1 | FRA | `l_3FJFUl` | 2021/2022 – 2026/2027 |

Post-MVP candidates from the same listing: Eredivisie `l_4H43wr`, Liga Portugal `l_4QexZg`, MLS `l_0eg6C9` (calendar-year seasons), Saudi Pro League `l_0Lpz1f` (from 2022/2023 only).

## Seasons and match lists

- **Season strings:** `"2025/2026"` for leagues that run autumn to spring, and `"2024"` for calendar-year leagues such as MLS. Take them from the `seasons` array in `/v1/leagues`; don't build them yourself. Some leagues have switched format: J3 League lists `"2024"`, `"2025"`, then `"2026/2027"`.
- **The `status` filter:** `played` (the default), `upcoming` or `all`. Upcoming matches have `status: "not_started"` and a null score.
- **Match IDs are stable** from fixture through to result. A played match fills in its full data after the final whistle.
- **No pagination (spike).** EPL 2025/26 `played` returned all 380 matches in one response: `{"league": {id, name, season, image_url}, "matches": [...]}`.
- **Match fields (spike):** `id`, `date`, `time_utc`, `status` (`"finished"` for played), `home_team` and `away_team` (each `{id, name, image_url}`), `score_home`, `score_away`. EPL 2025/26 check: 380 unique IDs, 20 teams × 38 matches, no null scores, 2025-08-15 to 2026-05-24.

## Player stat lines (`/matches/{id}/players`), shape verified by the spike

- **⚠️ The real shape differs from the docs.** `data` is a bare list of rows:

  ```
  {
    "player": {"id", "name", "position_id", "image_url"},
    "team_id": "t_…",
    "stats": [
      {"key": "top_stats", "title": "Top stats",
       "stats": {"<Display label>": {"key": "<stable key>", "stat": {"type", "value", "total"?}}, …}},
      …
    ]
  }
  ```

  The groups are a list, and each group's `stats` is an **object keyed by display label**. Each entry carries the **stable inner `key`**, with `type`, `value` and (for fractions) `total` nested one level down in `stat`. **Read by the inner `key`, never by the label**, as the docs say.
- **`player.position_id` is `null`** in this endpoint, as the docs warned. Positions come from lineups.
- **Quirks found in the keys:**
  - **Empty key:** `"Shotmap"` has `key: ""` with type `boolean`. Skip all `boolean` entries, and reject empty keys.
  - **Keys aren't snake_case names.** Examples: `"matchstats.headers.tackles"` (Tackles), `"big_chance_created_team_title"` (Big chances created), `"long_balls_accurate"` (Accurate long balls), `"duel_won"`/`"duel_lost"`, `"shot_blocks"` (Blocks), `"rating_title"` (Rating). Use them as opaque IDs and map each to a canonical metric in our registry.
  - **The same key can appear in more than one group.** `defensive_actions` is in both `top_stats` and `defense`. Dedupe by key when flattening a row, and fail loudly if two copies of one key disagree.
- **Groups seen:** `top_stats`, `attack`, `defense`, `duels` (more may exist; the analyzer lists them all).
- **Types seen:** `integer`, `double`, `fractionWithPercentage` (`value` out of `total`), `fantasyPoints`, `boolean`. The docs also list `distance`, `distanceWithPercentage` and `speed` (physical metrics, rarely present).
- **Rows include unused subs (spike).** 369 rows for 277 players who played; the other 92 have no stats. **A player played only if `minutes_played` is present.**
- **Keepers are laid out differently.** All their stats sit in `top_stats` (`saves`, `saves_inside_box`, `goals_prevented`, `expected_goals_on_target_faced`, `keeper_diving_save`, `keeper_high_claim`, `keeper_sweeper`, `punches`, `player_throws`, `goals_conceded`, …), with no `attack`/`defense`/`duels` groups. A **substitute keeper** gets keeper stats here but `goalkeeping: null` in the advanced endpoint. Parse by key and ignore the group.
- **Duplicate keys across groups:** 14 keys appear in two groups (`defensive_actions`, `touches`, `clearances`, `recoveries`, `interceptions`, `duel_won`, …). Across 277 rows the two copies never disagreed. Dedupe, and still fail loudly on a conflict.
- **Rating:** `rating_title` is present for the top 5 (26–31 rated players per match). It's **absent for players with ≤ 8 minutes** (18 rows: 1–8 minutes). An absent rating means "not rated", **not 0**. Match-level only; never per-90. The docs say ratings are missing entirely in the 26 leagues rebuilt in Sept 2026, none of which is in the top 5.
- **Not in the advanced endpoint, so this endpoint is still needed:** player xG, npxG (`expected_goals_non_penalty`), xGOT, xA, shots, goals, touches, touches in the opposition box, recoveries, fouls, and all goalkeeper shot-stopping.

### What a missing key or null means in `/players` (spike-verified, 258 outfield rows)

There's no single rule; it depends on the key. Every metric in our registry gets an explicit **absence policy**.

| Pattern | Keys | Meaning → what we store |
|---|---|---|
| Always present, zeros explicit | `goals`, `assists`, `minutes_played`, `matchstats.headers.tackles`, `interceptions`, `clearances`, `dispossessed`, `dribbled_past`, `fouls`, `recoveries`, `shot_blocks`, `touches`, `touches_opp_box`, `chances_created`, `defensive_actions`, `aerials_won`, `accurate_passes` | Value as given |
| **Omitted when zero** (never present with 0, often absent) | `total_shots`, `expected_goals`, `expected_goals_non_penalty`, `expected_assists`, `xg_and_xa`, `blocked_shots`, `big_chance_created_team_title`, `big_chance_missed_title`, `Offsides`, `corners`, `duel_won`, `duel_lost`, `headed_clearance`, `passes_into_final_third`, `shots_woodwork`, `clearance_off_the_line`, `last_man_tackle`, `errors_led_to_goal` | Missing → **0** (for played rows). Evidence: no scorer lacks `total_shots`, and these keys are never present with value 0 |
| **Present only when the parent count > 0** | `ShotsOnTarget`, `ShotsOffTarget`, `shot_accuracy` (when the player shot); fraction keys `accurate_crosses`, `dribbles_succeeded`, `long_balls_accurate`, `ground_duels_won` (when attempted) | Missing → successes 0, attempts 0, **success rate null**. Store numerator and denominator separately; compute rates from season sums |
| **Null value** | `was_fouled` (null in 130 rows, never 0); `expected_goals_on_target_variant` (xGOT, null in 69 rows, about the players whose shots were all off target) | Store null. `was_fouled` null may really mean 0: check it by comparing team sums against the opponents' `fouls`. Until then, treat it as unknown. xGOT null = "no shots on target" → contributes 0 to season sums |
| **Not rated** | `rating_title` | Missing → null, and the match doesn't count toward the rating average |

Keys aren't consistently named: `ShotsOnTarget`, `ShotsOffTarget` and `Offsides` are PascalCase. The registry maps every raw key to a canonical metric ID.

### Overlap with the advanced endpoint (same player, same match, 277 rows)

| Metric | `/players` key | Advanced field | Agreement |
|---|---|---|---|
| Tackles | `matchstats.headers.tackles` | `defending.tackles` | 277/277 identical |
| Assists | `assists` | `passing.assists` | 277/277 |
| Dispossessed | `dispossessed` | `carrying.dispossessed` | 277/277 |
| Interceptions | `interceptions` | `defending.interceptions` | **101 differ**, advanced usually higher (e.g. 3 vs 5): a different definition |
| Clearances | `clearances` | `defending.clearances` | **21 differ**, mostly by 1, in both directions |
| Chances created | `chances_created` | `creation.chances_created` (= `key_passes`) | **20 differ**, in both directions |
| **Minutes** | `minutes_played` | `minutes_played` | **269 differ.** `/players` gives 90 for a full match; advanced gives the true time including stoppage (e.g. 98). Subs are often off by 1 |

**Rules:**
1. Each canonical metric has **exactly one source** endpoint and key. Never mix the two endpoints' versions of a metric in one comparison.
2. **One minutes denominator for every per-90 rate,** whichever endpoint the numerator comes from: **`/players` minutes, where a full match = 90 (D-010).** Advanced minutes are stored for reference only.

## Advanced player analytics (`/matches/{id}/advanced/players`), spike-verified

- **Shape:** `{"players": [ row, ... ]}`. Each row has `player` (`id`, `name`, `shirt_number`), `team_id`, `minutes_played`, `actions`, and the groups `possession_value`, `passing`, `carrying`, `creation` (which includes `sca_breakdown`), `defending` and `goalkeeping`.
- **Sample:** 277 rows across the 9 matches, so about 31 per match: everyone who played, including subs. All 9 matches returned analytics, including the four non-EPL ones.
- **Fields:**

  | Group | Fields |
  |---|---|
  | `possession_value` | `pv_total`, `pv_offensive`, `pv_defensive`, `vaep_total`, `vaep_offensive`, `vaep_defensive`, `xt_total` |
  | `passing` | `passes`, `pass_accuracy`, `key_passes`, `assists`, `crosses`, `through_balls`, `switches`, `passes_into_box`, `progressive_passes`, `progressive_pass_distance` |
  | `carrying` | `carries`, `carry_distance`, `progressive_carries`, `progressive_carry_distance`, `carries_into_final_third`, `carries_into_box`, `take_ons`, `take_ons_won`, `dispossessed`, `miscontrols` |
  | `creation` | `chances_created`, `second_assists`, `sca`, `gca`, `xag`, `xg_chain`, `xg_buildup`, `sca_breakdown` (see below) |
  | `defending` | `tackles`, `interceptions`, `blocks`, `clearances`, `aerials`, `aerials_won`, `duels_won` |
  | `goalkeeping` | `distributions`, `distribution_accuracy`, `avg_pass_length`, `launches`, `launch_pct`, `claims`, `claims_won`, `claim_rate`, `sweeper_actions` |

- **Every non-goalkeeping field was present in every row.** No omissions, and no nulls except:
  - `passing.pass_accuracy`: null in 2 rows (players with no passes, so the denominator was empty).
  - `goalkeeping`: the whole object is null for outfield players (259 rows). It's present in exactly 18 rows, the 2 starting keepers × 9 matches. That confirms the docs.
  - `goalkeeping.claim_rate`: null in 6 of 18 rows (keepers with no claims). Null ≠ 0 holds.
- **✅ `sca_breakdown` is sparse, and a missing key means 0.** Keys seen: `pass_live`, `pass_dead`, `take_on`, `shot`, `defensive`. Keys with a count of 0 are omitted. The breakdown summed exactly to `sca` in 277/277 rows. This is the **one place where missing = 0**; everywhere else missing means "unavailable". `foul_drawn` never appeared at player level, which is consistent with the docs (team-only).
- **Numbers can be `int` or `float`** in the same field (e.g. `xg_chain`, `carry_distance`). Parse them as float.
- **✅ `chances_created` == `key_passes` in 277/277 rows.** The "assists + key passes" definition in the docs is wrong for the data. Use `key_passes` only; never add the two together. (`/players` also has a `chances_created` key. It differs from this one in 20/277 rows, so it's stored as a separate, alternative metric and never added to `key_passes`; D-012.)
- **Model outputs** (flag these in the UI): xT, VAEP, PV and xAG; xG chain and xG buildup are built on shot xG.
- **Carries are synthesized.** The source has no carry event; one is inserted between consecutive actions over 5 m.
- **Don't double-count:** `passing.passes` already includes `passing.crosses`.
- **SCA sums short at team level.** `sca_breakdown.foul_drawn` belongs to the team only, so the players' SCA adds up to less than the team total.
- **Unrated matches.** `ANALYTICS_UNAVAILABLE` (404) means the match exists but was never rated. That's normal, not an error. None occurred in the sample.

## Positions (lineups decoded by the spike)

- **Lineups shape:** `{"home": {formation, coach, starters[], subs[]}, "away": {...}, "home_team", "away_team"}`. There's one `formation` string per team, e.g. `"4-2-3-1"`, `"3-4-2-1"`, `"5-3-2"`. Each lineup entry is flat: `player_id`, `name`, `position_id`, `pitch_x`, `pitch_y`.
- **Starter `position_id` is a grid code: `row * 10 + column`.**
  - **Row** is the line, counted from the goal: 1 = GK, 3 = defensive line, 5 = wing-back line, 6 = holding midfield, 7 = central midfield, 8 = attacking midfield, 10 = forward line, 11 = lone striker.
  - **Column** runs 1–9 across the pitch; 5 is central. **✅ Low columns are the RIGHT flank, high columns the LEFT.** Checked against known players: Frimpong (RB) = 32, Kerkez (LB) = 38, Calafiori (LB) = 38 twice, Salah (RW) = 83, Saka (RW) = 103, Rice (left CM in a 4-3-3) = 77.
  - **`pitch_x`** (depth) and **`pitch_y`** (width) are fixed by the slot, not measured; high `pitch_y` = right. The same slot gives identical coordinates in every match: GK always `(0.1, 0.5)`; slot 32 always `y = 0.88`, 38 always `y = 0.13`. They add nothing beyond `formation` + `position_id`.
- **Slots seen across the 9 formations in the sample:**

  | Formation | Slots |
  |---|---|
  | 4-2-3-1 | 11 / 32 34 36 38 / 64 66 / 83 85 87 / 115 |
  | 4-3-3 | 11 / 32 34 36 38 / 73 75 77 / 103 105 107 |
  | 4-4-2 | 11 / 32 34 36 38 / 72 74 76 78 / 104 106 |
  | 4-1-4-1 | 11 / 32 34 36 38 / 65 / 82 84 86 88 / 115 |
  | 4-1-2-3 | 11 / 32 34 36 38 / 65 / 84 86 / 103 105 107 |
  | 3-4-2-1 | 11 / 33 35 37 / 62 64 66 68 / 84 86 / 105 |
  | 3-4-3 | 11 / 33 35 37 / 72 74 76 78 / 103 105 107 |
  | 5-3-2 | 11 / 33 35 37 + 51 59 / 73 75 77 / 104 106 |

  Wing-backs show up in different places depending on the formation: row 5 columns 1/9 in 5-3-2, but row 6 columns 2/8 in 3-4-2-1 and row 7 columns 2/8 in 3-4-3. So the role has to come from **formation + slot**, not the slot alone.
- **Substitutes:** coordinates `(0, 0)`, and `position_id` is the broad category (0 = GK, 1 = DEF, 2 = MID, 3 = ATT). The sample had 172 sub rows (unused bench included): 21 / 49 / 53 / 49. No `-1` (unclassified) seen. Starters: 198 = 9 × 22 ✓.
- **Approach (now viable):**
  1. Map (formation, slot) → detailed role (GK, CB, FB, WB, DM, CM, AM, W, ST), plus side (L/C/R) from the column, with a rule table and tests.
  2. Starters get that role. Subs get only their broad category.
  3. A player's season position is their most-played role, weighted by minutes.
  4. The profile `position_id` is a fallback, and a "listed position" for display only.
- **Player profiles (`/players/{id}`, spike, 28 players):**
  - Fields: `id`, `name`, `short_name` ("F. Wirtz", useful for search), `shirt_number` (**a string**, e.g. `"7"`, unlike the integers elsewhere), `position_id`, `country_code`, `image_url`.
  - **`position_id` uses the same grid codes as lineups**, as the player's listed primary slot, and usually agrees on role: Wirtz 85, Alisson 11, Kerkez 38, Frimpong 32, van Dijk 36, Konaté 34, Mateta 105.
  - **But it's unreliable:** some players get only a broad category (Alex Scott `2`, Senesi `1`, Diakité `1`); the side can be wrong (Gakpo is listed at 83, right, but started at 87, left); and strikers move between 104/105/115.
  - So lineups (weighted by minutes) are the source of a player's role. The profile is a fallback for players with no starts, and a label.
- **Limitation:** lineups give the *starting* shape only. Tactical switches and positional changes during a match aren't visible.

## EPL 2025/26 full-season crawl (2026-11-20)

- **Load:** 1 match list + 380 × 3 match responses + 537 profiles = **1,679 responses**. At 1 request/s the run completed with no failures. Every match had players, advanced analytics and lineups; none returned `ANALYTICS_UNAVAILABLE`.
- **Profiles:** 537 players, **0 not found**.
- **`/players` rows:** 15,193 across 380 matches (about 40 per match including unused subs).
- **Formations:** 19 distinct, by team-matches: 4-2-3-1 432, 3-4-2-1 93, 4-3-3 85, 4-1-4-1 31, 3-5-2 19, 3-4-3 18, 4-4-2 16, 5-4-1 15, 5-3-2 13, 4-4-1-1 8, 3-4-1-2 6, 4-1-3-2 6, 4-1-2-3 4, 4-3-2-1 4, 4-5-1 4, 4-3-1-2 2, 4-2-2-2 2, 5-3-1-1 1, 3-5-1-1 1. New slots seen: 71/79 and 73/75/77 in row 7 for five-man midfields, 63/65/67 for a flat three in row 6, and **95** (row 9) for a player just behind a lone striker. All mapped in SCHEMA.md §7 (D-013).
- **Five `/players` keys not in the spike's match** (checked with `scripts/check_new_keys.py` over all 15,193 rows). Each is **only sent when it's at least 1**; a 0 never appears, so a missing key means 0:

  | Key | Rows | Values | Keepers |
  |---|---|---|---|
  | `penalties_won` | 69 | 1 ×68, 2 ×1 | 0 |
  | `conceded_penalties` | 92 | 1 ×92 | 12 |
  | `missed_penalty` | 15 | 1 ×15 | 0 |
  | `owngoal` | 39 | 1 ×38, 2 ×1 | 4 |
  | `saved_penalties` | 11 | 1 ×11 | 11 (keepers only) |

- **Own goals are not counted in `goals`:** in the 39 rows with an own goal, `goals` is 0 (35) or absent (4). No double counting.

## Errors

| Status | Code | What we do |
|---|---|---|
| 400 | `INVALID_PARAMETER` | Bug on our side: fail loudly |
| 401 | `UNAUTHORIZED` | Bad or missing key: abort the sync |
| 404 | `RESOURCE_NOT_FOUND` | The ID doesn't exist. Log it. For per-player advanced data, it means the player took no part in the match |
| 404 | `ANALYTICS_UNAVAILABLE` | Negative-cache it with a re-check date; mark the match "not rated" |
| 404 | `HALVES_UNAVAILABLE` | Not used |
| 429 | `RATE_LIMIT_EXCEEDED` | Wait for `Retry-After`, then retry |
| 500 | `INTERNAL_SERVER_ERROR` | Back off and retry |

The SDK also maps `SUBSCRIPTION_SUSPENDED` and `PLAN_UPGRADE_REQUIRED`, which are left over from the old paid tier.

## SDK 0.1.6 caveats (from reading its source)

- **Its dataclass models default missing counts to `0`** (e.g. `passes: int = 0`). Parsing through them breaks null ≠ 0. **We parse raw JSON with our own Pydantic models.**
- **Raw `data` is only reachable through the private `client._get(path, params)`.** The public methods convert everything to dataclasses.
- **Retries:** up to 2 by default, on 429/500/502/503/504 and network errors. It honours `Retry-After` exactly; otherwise it backs off exponentially with jitter, capped at 8 s.
- We don't use the SDK; see D-009.

## Other things to know

- **Team stats are display strings.** `/matches/{id}/stats` returns values like `"12 (55%)"`, to be parsed according to `format_type`. Use `/advanced` when you need typed numbers.
- **Pitch coordinates:**
  - Shots are in metres on a 105 × 68 pitch, normalized so the attack always goes towards x = 105.
  - Lineup coordinates run 0–1 and are fixed per formation slot (see Positions).
- **Heatmaps:**
  - A 16 × 12 grid of sparse `[cell_x, cell_y, actions]` triples; an absent cell means no actions there.
  - The default frame (`acting_ltr`) shows each team attacking in its own direction. Use `frame=home_ltr` to put both teams on one pitch.
- **IDs changed in Sept 2026 for the 26 rebuilt leagues.** Matters only if we ever cache those leagues from before that date.

## Status of the spike's questions

- [x] Shape of `/v1/leagues`, top-5 league IDs and their season ranges
- [x] Match-list shape: one unpaginated response per season
- [x] `chances_created` == `key_passes` (277/277)
- [x] Advanced-analytics field inventory and its null/omission behaviour
- [x] `sca_breakdown` sums to `sca` (277/277), so a missing key means 0
- [x] Lineup slot scheme (`row * 10 + column`; coordinates fixed per slot)
- [x] Which flank is which: low column / high `pitch_y` = right
- [x] Real shape of `/players` stat entries: stable inner `key`, with `type`/`value`/`total` under `stat`
- [x] Advanced analytics available for a recent match in each top-5 league (1 match each)
- [x] Full `/players` key inventory, ratings coverage (all 9 matches rated; ≤ 8 min → no rating)
- [x] What a missing key means in `/players`, per key (see the absence table)
- [x] `/players` vs advanced agreement on overlapping metrics, including the minutes difference
- [x] Profile `position_id`: same grid codes, but unreliable, so fallback only
- [x] Every formation in EPL 2025/26 (19), all mapped (crawl)
- [x] The five penalty/own-goal keys: sent only when ≥ 1, so missing = 0 (crawl)
- [ ] Whether `was_fouled` null means 0 (compare team sums with the opponents' `fouls`; low priority)
- [ ] Whether advanced analytics ever appear later for a match that returned `ANALYTICS_UNAVAILABLE` (needs weeks of observation)
- [ ] **Terms of use and data license.** The docs don't include them. Ask PitchAPI support before hosting (D-007)