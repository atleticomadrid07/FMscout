# DECISIONS.md

Each entry: date, decision, why, and the alternatives that were rejected.

## D-001 · 2026-11-18 · Stack: FastAPI + React (option B)
- **Decision:** Python backend (FastAPI, SQLAlchemy, pandas) and React + TypeScript frontend.
- **Why:** The stats core is the hardest part, and Python is the stronger tool for it. The API boundary keeps the layers clean. React has no ceiling for the later polish phase. The user also wants to build fluency in Python and React.
- **Rejected:** A (all Python with Streamlit): fastest to an MVP, but the UI would need rewriting for polish. C (all TypeScript with Next.js): weaker stats tooling, and the TypeScript SDK is very new (Oct 2026).

## D-002 · 2026-11-18 · SQLite to start, with Alembic migrations
- **Why:** Zero ops, and the workload is mostly reads. Alembic keeps the schema portable to Postgres if hosting needs it.
- **Rejected:** Postgres from day one. That's operational overhead before it's needed.

## D-003 · 2026-11-18 · MVP covers the top 5 leagues, and the crawl starts with EPL
- **Why:** Gives a vertical slice that's small enough to verify by hand. Each league after that is incremental.
- **Note:** Peer groups are EPL-only until other leagues are added. The UI must show the peer group.

## D-004 · 2026-11-18 · Caching: the local DB is the app's only read path
- **Decision:** The web app reads only from our own database. Only the sync CLI calls PitchAPI. Four layers:
  1. **Raw response cache** keyed by endpoint + normalized parameters, with status, error code, fetched_at and expires_at.
  2. **TTL per endpoint class:** finished-match data is never re-fetched. Current-season match lists are refreshed on each sync. Completed-season match lists are never re-fetched. Player and team profiles are fetched the first time an ID is seen, then re-checked on a long TTL.
  3. **Negative cache:** `ANALYTICS_UNAVAILABLE` and `HALVES_UNAVAILABLE` are stored as results, with a re-check date.
  4. **Materialized aggregates and percentiles**, rebuilt after each sync.
- **Why:** Search and comparison never trigger API traffic, which keeps us polite under fair use. A hosted site can't leak the key or burn the burst limit. Re-normalizing doesn't require re-crawling.
- **Rejected:** Calling the API on demand with an HTTP cache in front. Search needs a local index regardless, and that approach scales API load with the number of visitors.

## D-005 · 2026-11-18 · Post-match data only
- **Decision:** No live data and no `/live` route. Scouting and comparison don't need it.

## D-006 · 2026-11-18 · FM: FM24 `.rtf` is the primary import; `.fm` is a stretch goal
- **Decision:** After the PitchAPI metric list is fixed (end of Phase 2), the user builds a matching FM24 view and exports it. The mapping is based on that real file.
- **Rejected:** Designing the FM parser before we have a sample. The format can't be verified without one.

## D-007 · 2026-11-18 · Hosting comes later, gated on a terms-of-use check
- **Decision:** It will be hosted eventually on the user's website. Before deploying, confirm that PitchAPI's terms allow caching and display of their data. The review we found states no data license, and neither do the official docs (checked 2026-11-19).
- **Update 2026-11-20:** the user is unsure whether to host at all. Local-only for now; nothing in Phases 2–6 depends on the choice.

## D-008 · 2026-11-19 · Repo structure: one repo, `src/` layout, layered packages
- **Decision:**
  - One repo containing `backend/` and `frontend/`.
  - The backend is a single `scouting` package in a `src/` layout, with one subpackage per layer: `config`, `domain`, `adapters/{pitchapi,fm}`, `db`, `ingest`, `metrics`, `mappings`, `api`, `cli`.
  - The dependency rules in `docs/ARCHITECTURE.md`; in particular, `api` never imports `adapters`.
  - `frontend/` is created by the Vite scaffolder when it's needed, in Phase 3/4.
- **Why:** The folders mirror the architecture's layers, so a layer violation shows up as a visibly wrong import. The `src/` layout makes tests run against the installed package.
- **Rejected:**
  - Separate repos for backend and frontend: overhead for a solo project.
  - A flat layout: import mistakes hide.
  - One package per layer: overkill at this size.

## D-009 · 2026-11-19 · Fetching: our own httpx client, not the SDK
- **Decision:** `scouting.adapters.pitchapi.client.PitchApiClient`, a small synchronous httpx client. It returns the raw `data` payload untouched, validates the `{data}` / `{error}` envelope, throttles to 1 request/s, and retries 429/5xx/network errors (honouring `Retry-After`, otherwise exponential backoff with full jitter). 404s come back as results (`ok == False`) for the negative cache; 401, other 4xx and malformed envelopes raise. A `Retry-After` over 300 s raises, so the crawl stops and resumes later instead of blocking.
- **Why:** The SDK's dataclasses default missing counts to 0, which breaks null ≠ 0, and its raw JSON is only reachable through the private `_get`, which can change in any 0.1.x release. The client is about 300 lines and fully unit-tested with `httpx.MockTransport`. The Phase 0 spike exercises it before Phase 1 builds the cache on top.
- **Rejected:**
  - SDK `client._get()`: a private API, and it doesn't fit where our cache sits.
  - A throwaway httpx script for the spike: Phase 1 would rebuild the same thing.

## D-010 · 2026-11-19 · Per-90 uses `/players` minutes (a full match = 90)
- **Decision:** The canonical minutes for every per-90 rate is `minutes_played` from `/matches/{id}/players`, where a full match counts as 90. This holds whichever endpoint the numerator comes from. The advanced endpoint's minutes (actual time including stoppage, e.g. 98) are stored as `appearance.minutes_actual` for reference only.
- **Why:** It's the convention public scouting sites use, so our per-90s line up with what people expect. It's present for every player who played, including substitute keepers. Using one denominator everywhere keeps metrics from the two endpoints comparable. The spike found the two minutes values differ in 269 of 277 rows, which would mean about an 8% difference in per-90s for a full-match player.
- **Rejected:** Advanced minutes (true time on the pitch): a more exact measure of exposure, but per-90s come out about 8% lower than the usual convention, so comparisons with other sites look off.

## D-011 · 2026-11-19 · Canonical schema v1.1 approved (SCHEMA.md)
- **Decision:** SCHEMA.md v1.1 is the data model. The answers to its open questions:
  1. **Long `stat_value` table** (one row per player-match-metric), driven by a typed metric registry.
  2. **A separate `raw.db`** holding the gzip-compressed API responses.
  3. **7 peer roles:** GK, CB, FB (+WB), CM (+DM), **AM (central 10s, their own group)**, W (+wide AMs and wide midfielders), ST. `formation_role` is an explicit per-formation mapping, because the same slot (e.g. 84/86) is a 10 in one formation and a CM in another.
  4. **Season rating** = simple mean over rated matches. Low priority; not in the default radars.
  5. **Minimum minutes for percentiles:** 25% of the possible minutes by default, adjustable in the UI. Players can be added to a comparison manually; they're ranked against the peer group without being added to it, and they're flagged.
  6. **Unused substitutes** aren't stored.
  7. **A mid-season move within one competition** gives one row per player per competition-season, with a per-team breakdown.
  8. **FM's unique ID in `.rtf` exports** is checked in Phase 6 with a real file.
- **Why:** These follow from the spike's findings (PITCHAPI_NOTES). A separate AM group keeps 10s from being judged against deeper CMs (e.g. Nico Paz vs Barrios). Ranking manually added players without adding them keeps everyone else's percentiles stable.
- **Rejected:**
  - A wide table, one column per metric: every new metric needs a migration, and FM stats don't fit it.
  - Six peer roles with AM inside CM: rejected by the user.
  - Deriving roles from the slot alone: wrong for 84/86.
- **Update 2026-11-20 (D-016, D-017):** the CM group is to be split into 6s (DM) and 8s (CM) by heatmap depth, giving 8 peer roles (slice 2d; rule in D-018).

## D-012 · 2026-11-20 · Full metric registry (90 metrics) and its overlap rules
- **Decision:** The registry grows from 18 to 90 metrics in slice 1d: attacking, creating, carrying, defending, possession value and goalkeeping. The user's picks:
  - **Added from `/players`:** `chances_created` and `defensive_actions`. `chances_created` is an alternative definition, not a duplicate: it's never added to `key_passes`.
  - **Added from advanced:** the five `creation.sca_breakdown` parts (they sum to `sca` in 277/277 spike rows, so a missing part is 0), and the keeper rates `distribution_accuracy`, `launch_pct`, `claim_rate`, `avg_pass_length`.
  - **xA and xAG are both kept** (`/players` `expected_assists` and advanced `creation.xag`). The user picks one for the radars later. They're different models and are never combined.
  - **Where both endpoints count the same thing, `/players` is the source:** interceptions, clearances, duels and aerials. It covers every player who played (including when advanced analytics are unavailable), and it's the same source as the per-90 minutes (D-010).
- **Per-match rates are stored weighted** (`metric.value_is_rate`): value = rate × weight, denominator = weight (distributions or claims), so a season is an exact weighted average through the normal fraction rule. They're never per-90'd. Advanced two-field fractions use `metric.denominator_key`.
- **Why:** Collecting the full metric set during the first crawl means Phase 2 never needs a re-crawl, only `--offline --renormalize`. One source per overlapping stat avoids double counting and silent mixing of two providers' definitions.
- **Rejected:**
  - Averaging per-match percentages over a season: wrong whenever volume differs between matches.
  - Comparing the two endpoints' overlapping stats first: the user accepted the recommendation without it.
  - Picking one of xA/xAG now: the user wants to decide when building the radars. (Decided in D-016: xA.)

## D-013 · 2026-11-20 · After the EPL 2025/26 crawl: 11 more formations, 5 more metrics
- **Formations:** `report formations` found 19 formations in the season; the 11 not in the spike now have explicit mappings (SCHEMA.md §7). The user's judgement calls:
  - **Slot 95** (behind a lone striker in 4-4-1-1, 3-5-1-1, 5-3-1-1): **AM**.
  - **4-1-3-2's 83/87:** **CM** (a narrow diamond).
  - **4-3-2-1's 84/86:** **AM**, as in 3-4-2-1. (3-4-2-1's became W in D-016; 4-3-2-1's stay AM.)
  - **5-4-1's 72/78:** **W**, following D-011 (wide midfielders go with wingers).
- **Metrics (95 total):** `/players` keys not seen in the spike's match, all added as counts: `penalties_won`, `penalties_conceded` (all players), `penalties_missed`, `own_goals` (all players), `penalties_saved` (keepers only). "Missing means 0" confirmed on all 15,193 rows of the season (PITCHAPI_NOTES).
- **Process fixes:** the profile step commits every 25 players, prints progress, handles Ctrl-C and skips a malformed profile; per-match warnings moved to DEBUG because the CLI summary already reports them once with counts.
- **SQLite migration rule:** use plain string server defaults (`server_default="0"`), never `sa.text(...)` or `false()`. Alembic rebuilds the whole table for a SQL-expression default, and with foreign keys on, that fails once other tables reference it.
- **Rejected:** slot 95 as ST, 4-1-3-2's 83/87 as W.

## D-014 · 2026-11-20 · Phase 2 design: aggregation and percentile rules
- **Aggregation (slice 2a, built and hand-checked on EPL 2025/26):**
  - **Exposure minutes per metric** = minutes of the appearances with a row for that metric. Matches without advanced analytics add no minutes to advanced metrics; keepers' minutes never count for shots.
  - **A stored NULL** follows the registry's `season_null`: `skip` (unknown: adds no total and no minutes; the default) or `zero` (a known zero: `xgot`, NULL = no shot on target; `was_fouled` since D-016).
  - **per90** = total ÷ exposure minutes × 90; **rate** = Σ successes ÷ Σ attempts (the exact weighted average for keeper rates). NULL when undefined.
  - **Primary role** = most minutes as a starter (ties: more starts, then role order). No starts: the profile's listed slot, only where every mapped formation agrees on it (`role_source = 'listed'`); otherwise no role.
  - **Rebuilt per season in one transaction**, deterministic (`computed_at` = latest `normalized_at`), so `report checksum` stays stable. Runs automatically after a `sync season` that leaves nothing pending, and on demand with `scouting aggregate`.
- **Percentiles (slice 2b):**
  - **Formula: midpoint.** pct = (players below + ½ × players tied) ÷ n × 100. Tied players (mostly zeros in rare-event stats) sit in the middle of their block, not at its bottom or top.
  - **Eligibility per metric:** per-90 metrics need the minimum minutes (855 for a full EPL season) on that metric's own exposure; rate metrics need a minimum number of season attempts. Approved starting values (tune with `report thresholds`): passes 200 (100 since D-019), long balls 20, crosses 20, dribbles 15, take-ons 15, ground duels 30, aerials 15, claims 10 (`claims_won`, `claim_rate`), distributions 100 (`distribution_accuracy`, `launch_pct`, `avg_pass_length`). No minimums on count totals: cutting off low performers would skew everyone else's percentiles.
  - **Stored in the `metric` table:** two new columns, the minimum attempts and a direction (higher / lower / none), so the API and UI can explain an exclusion ("needs 200 passes"). Safe migration: added columns with string defaults (D-013 rule).
  - **Excluded players get no percentile row**; the UI derives the reason from the season stats. They can still be compared: the on-the-fly "rank against a group" function places them against the peer group without adding them to it, flagged.
  - **Each percentile stores the number of players it was ranked among**, since eligibility can differ per metric within one peer group.
  - **Stored raw:** low value = low percentile for every metric. The UI flips lower-is-better metrics. "No direction" (not coloured good or bad): `launch_pct`, `avg_pass_length`, `corners`.
  - **Small peer groups:** warn below 20 players, but still show the percentiles. (EPL 2025/26 after D-016: AM 17, accepted with the warning, D-017.)
  - **Bases:** per-90 for counts, model outputs and distances; rate for fractions. No percentiles on season totals (they mostly measure minutes).
- **Why:** Matches the scouting requirements (explicit peer group, sample size always shown, null ≠ 0), and keeps the UI from scanning `stat_value`.
- **Rejected:**
  - `percent_rank` (puts all tied zeros at 0th) and cumulative distribution (puts them at the top of their block, so "did nothing" can look above average).
  - Statistical shrinkage for small samples: more accurate, but overkill for now.
  - Storing percentiles pre-flipped: the user prefers the UI to flip.
  - Thresholds in code only: the UI couldn't explain an exclusion.
  - Larger peer groups by pooling leagues or seasons: adding leagues doesn't enlarge a one-league peer group by itself. Pooling the top 5 is a Phase 5 option (backlog).

## D-015 · 2026-11-20 · Position templates measure process, not outcomes (slice 2d → now 2e)
- **Decision:** one template per peer role (GK, CB, FB, CM, AM, W, ST; plus DM once the 6/8 split lands, D-016), about 12 metrics in sections, in `mappings/position_templates.yaml` (TOML since D-019). They drive the radars and the comparison presets. Templates use **underlying / process metrics**: npxG, xGOT, shots, touches in the box, xA, key passes or chances created, SCA, xT, VAEP, progressive actions, defensive actions and duel success; for keepers, goals prevented, claims and distribution. **Outcomes stay out of templates** (goals, assists, GCA, big chances missed, goals conceded) and are shown prominently elsewhere on the scouting page.
- **Why:** the user's scouting view: two strikers with 10 npxG are getting into the same positions whether they scored 5 or 11; finishing varies. Templates also fix the metric list the product uses, which becomes the FM24 view's column list (D-006), and they settle the near-duplicate pairs (`xa`/`xag` and `claims_won`/`claim_rate`: settled in D-016; `dribbles_completed`/`take_ons_completed`, `key_passes`/`chances_created`: settled in D-019).
- **Caveats:** model outputs are flagged as models in the UI. PitchAPI gives no penalty goals, so "goals vs npxG" would favour penalty takers; the fair finishing comparison on the page is **goals vs xG** (both include penalties).
- **Process:** the user picks the template metrics, from option lists per position that Claude prepares (with eligibility counts from `report thresholds`), rather than receiving finished templates.
- **Rejected:** leaving templates to Phase 4: the metric list is needed for the FM workflow at the end of Phase 2, and the templates can be checked against real eligibility counts now.

## D-016 · 2026-11-20 · Slice 2c: what the disputed numbers mean, and the calls made
Evidence from `scripts/check_2c.py` on EPL 2025/26 (read-only, run by the user).
- **`was_fouled`: NULL means 0.** Among 10,725 outfield rows a 0 is never stored (5,792 NULL). Per match, the team's `was_fouled` (NULL as 0) vs the opponent's fouls committed: gap 0 in 54.7% of 760 team-matches, |gap| ≤ 1 in 88.2%, never negative. The mean gap stays flat as the NULL count grows (0.58 at 4–6 NULL rows, 0.59 at 7–9, 0.66 at 10+); if NULLs hid fouls it would rise by about 1.6 per extra NULL row. The steady residual (~0.6 per team-match, 452 over the season) is fouls on keepers (who have no `was_fouled` row) and, probably, fouls with no fouled player (not verified). **Change:** `season_null = "zero"` for `was_fouled`; stored values stay NULL, the meaning is applied at aggregation (as for `xgot`). The metric means "fouls suffered by outfield players".
- **Keeper rates are their counts.** `launch_pct` = launches ÷ distributions × 100, rounded to 0.1 (760/760 within ±0.05). `claim_rate` = claims_won ÷ claims × 100, rounded to 0.1 (419/419; 341 rows had no claims). Both kept; **templates use `claims_won`** (exact counts) over `claim_rate`; `launch_pct` is fine as the style metric (our season value is within 0.05 points of the exact share).
- **Roles:**
  - **3-4-2-1's 84/86 → W** (was AM). Of the 20 most-used starters there, about 13 are wingers by season role (Johnson, Cunha, Amad, Edwards, Okafor…); Sarr, Pino and Mbeumo were in the AM group only because of this mapping.
  - **4-3-2-1's 84/86 stay AM** (the user chose 3-4-2-1 only).
  - **4-2-3-1's 64/66 stay DM; slot 95 stays AM** (10 starts all season).
- **xA (not xAG) for templates.** Definitions (PitchAPI docs, via the user): **xAG** = the xG of the shots a player's passes produced, credited to the passer, penalties excluded; **xA** = the modelled likelihood that any completed pass becomes an assist. Data: equal to 2 decimals in only 28.5% of matches (r = 0.77), but season totals almost identical (682 vs 678); within peer groups, percentiles differ by 8–18 points on average. xA chosen for coverage (54 EPL rows have xA but no advanced row, and other leagues have matches without advanced analytics) and because it shares an endpoint with `npxg`. **No xA/xAG on the CB template:** CB values are ~0.02–0.04 per 90, so their percentiles are mostly noise.
- **Display:** `inspect player` shows "85th (n=57)" instead of "85 of 57".
- **Next: 6s and 8s as separate peer groups** (user's call; slice 2d, D-017). Central midfield splits into DM (6s) and CM (8s); AM stays the 10s; 8 peer roles. Players are classed by **average position (depth of touches) from heatmaps**, because formation slots can't tell them apart: the 4-2-3-1 double pivot mixes Caicedo, Casemiro and Zubimendi (6s) with Mac Allister, Gravenberch and Anderson (8s). Templates move to slice 2e and get a DM template.
- **Rejected:**
  - Keeping `was_fouled` NULL as unknown.
  - 3-4-2-1's 84/86 as AM; changing 4-3-2-1's as well.
  - xAG for templates.
  - 6/8 by slots only (half of every double pivot misclassed), by stat profile (circular: classing players by defensive output and then ranking that output within each class flattens the percentiles), or one central-midfield group with separate 6/8 templates (the user wants separate percentile groups).
- **Results of the closing run (2026-11-20, user's machine):**
  - `sync season --offline --renormalize`: peer groups (≥ 855 min) **GK 25 · CB 67 · FB 62 · CM 76 · AM 17 · W 65 · ST 33** (before: CM 75, AM 25, W 57, ST 34; 345 ranked players either way); 25,034 percentiles. Salah `was_fouled`: 16 in 2,148 min = 0.6704 per 90, 13th among 65 Ws.
  - **`/players` `expected_assists` is real xA** (check 5): in rows with 0 key passes, xa > 0 in 58.8% (17.9% of season xA), while xag > 0 in 0.0%, as its shot-based definition requires. Label it "xA". The docs' "xA is not served" refers to the advanced endpoint.

## D-017 · 2026-11-21 · Slice 2d design: 6s and 8s split by heatmap depth
- **Spike (`spikes/heatmap_spike.py`, 3 EPL matches, 9 requests):** `GET /matches/{id}/heatmaps` returns a grid for **every player who played, in one request per match** (+380 requests per EPL season, ~6.5 min), in a frame where every team attacks towards high x. Go for approach C. Both anchor pairs came out the right way round in single matches (Caicedo 0.470 vs Enzo 0.545; Adams 0.391 vs Scott 0.525), but single-match gaps are small, so the label must come from a season average (PITCHAPI_NOTES).
- **Depth:** per appearance, the actions-weighted mean of the heatmap cell centres along the pitch, scaled 0 (own goal line) to 1 (opponent's). Stored on `appearance` (`depth`, `depth_actions`), not as a registry metric: it describes where a player plays, it isn't a stat to rank. Raw cells stay in raw.db for Phase 8 visuals.
- **Sync:** heatmaps are a 4th per-match request with their own `match.heatmaps_status` (`pending` / `ok` / `missing`). An offline run that never fetched them leaves them `pending` (no error), so `--offline --renormalize` keeps working; one online `sync season` then fetches only the missing heatmaps. Pending heatmaps don't hold back aggregates. Only the default frame (`acting_ltr`) is accepted.
- **Measure (user's choice):** **team-relative depth** decides each player's label; the **partner-relative rank** (deepest of the team's starting central mids) is a cross-check, reported but never deciding. The spike showed why: partner rank alone would have called Kamara (next to Onana) and Szoboszlai (level with Mac Allister) 8s.
- **Season label:** minutes-weighted over the player's **starts in central midfield** (DM/CM roles) that have heatmap data. **At least 5 such starts** (user approved); otherwise a formation fallback (mostly a single pivot → DM, else CM), with the method recorded per player.
- **Decided at 2d-2 from season data (user's choice):** the reference point (team outfield average vs the team's centre-back line) and the 6/8 cut, calibrated on the user's anchors. **Anchors** (provisional; the user: "don't take my words as 100%", so they move if their data says they play higher or deeper): 6s Caicedo, Tyler Adams, Xhaka, Garner, Baleba, Berge, Ugarte; 8s Bruno Guimarães, Enzo Fernández, Alex Scott, Tielemans, Stach, Gallagher, Mac Allister. Each must be in the central-mid group with enough minutes (Tielemans may be mostly AM). (Garner moved to the 8s in D-018.)
- **Build order (approved):** 2d-1 fetch heatmaps + store per-appearance depth (built 2026-11-21); 2d-2 read-only calibration report; 2d-3 classification + DM peer group + migration that drops and recreates the derived peer tables. One zip per step, each explained.
- **AM 17 accepted** with the small-group warning; pooling across leagues (Phase 5) grows it.
- **Migration rule applied:** the new columns are plain `ADD COLUMN`s; `heatmaps_status` has **no CHECK** (adding one rebuilds `match`, which `appearance` references: ERRORS.md); its values are set only by the sync.
- **Rejected:**
  - The pass network's average positions: covers starters only, up to the first substitution (a valid fallback; r = 0.94–0.97 with heatmap depth).
  - The `home_ltr` frame: it flips the away team.
  - Absolute depth (team style shifts whole teams up or down) and partner rank as the decider.
  - A registry metric for depth: it would get percentiles it shouldn't have.

## D-018 · 2026-11-21 · The 6/8 rule: centre-back reference, 14 m cut, deeper-partner override at 2 m
Evidence: `scouting report depth` on EPL 2025/26 (slices 2d-2 and 2d-2b, run by the user), 76 central mids with ≥ 855 min, 75 of them with ≥ 5 central-mid starts with heatmaps.
- **Reference: the team's starting centre-backs.** A player's depth in a match = his depth minus the mean depth of his team's starting CBs; season = minutes-weighted over his central-mid starts. The **outfield-average reference is kept as a second opinion** (shown in reports, never deciding). Why: with the CB line the anchors separate by 2.6 m (Berge +12.6 m vs Scott +15.2 m) against 0.4 m with the outfield average, and Rice comes out an 8 (+14.3 m), correct as Arsenal's left 8 (outfield: a 6). The user asked whether to combine the two; a simple average narrows the anchor gap to ≈ 1.7 m and flips Rice back, so it dilutes the better signal.
- **Cut: 14 m in front of the CBs** (0.1333 of the pitch length): below = 6 (DM), at or above = 8 (CM). The anchors alone would cut at +13.9 m with nobody on the wrong side (margin 1.3 m). The distribution is one smooth bell with no natural gap, so players within **~1 m of the cut are shown as borderline**.
- **Anchors updated:** Garner moved to the 8s (+17.8 m, the user agreed). All 14 anchors sit on the right side.
- **Deeper-partner override, G = 2 m (user's pick).** Partner gap per match = his depth minus the depth of his deepest fellow starting central mid (+ = a deeper partner); season = minutes-weighted over starts beside a partner. **A 6 by depth whose partner gap is ≥ 2 m over ≥ 5 starts beside a partner becomes an 8** (method recorded as `partner`). It uses the size of the gap, not the rank, so close double 6s stay 6s. The user's case: "depth decides, but Bernardo should be an 8". Data: among the 30 6s by depth, partner gaps are João Gomes +4.7 m, Bernardo Silva +3.5 m, then Ayari +0.3 m, Henderson +0.2 m and everyone else ≤ −0.1 m: an empty band from +0.3 to +3.5 m. 2 m sits in its middle (1.5 m clear of Bernardo, 1.7 m clear of Ayari) and matches what 8s beside a 6 show (Scott +2.0, Rice +2.3, Mac Allister +2.4). Gravenberch, Onana, Andrey Santos, Zubimendi, Rodri and Nico González never flip at any tested G. Result on EPL 2025/26: **DM 28 · CM 48** (Bergvall, 4 starts, by formation → CM).
- **Formation fallback as built (D-017's wording):** a 6 only if more than half of his central-mid minutes came as the only DM-role starter in his side (a single pivot); 4-2-3-1 double-pivot DMs count as 8s. The user had no objection when told this differed from the 2d-3 plan's "DM slot → 6".
- **Accepted as 8s:** Kamara (+15.1 m) and Tonali (+16.7 m) (user: "they do play that fairly often"). The override only moves 6s to 8s; there's no 8→6 rule, and no single cut makes Rice an 8 and Kamara a 6.
- **Stored and shown:** season depth, partner gap, the starts behind each, and the method (`depth` / `partner` / `formation`) per player on `player_scope` (slice 2d-3). Shown with the label, e.g. "a 6 (DM) by depth: 9.5 m in front of his centre-backs". **In the UI (Phase 4) this depth display must be removable/toggleable** in case it looks out of shape.
- **Re-check per league:** the 14 m cut and G were calibrated on one league-season; `report depth` stays as the tool to re-check them when other leagues or seasons are loaded (Phase 5).
- **Rejected:**
  - Outfield-average reference, or an average of both references (see above).
  - G = 3 m (same result, but Bernardo only 0.5 m clear) and G = 4–5 m (Bernardo stays a 6).
  - A rank-based partner rule ("rarely the deepest → 8"): would split close double 6s like Onana/Kamara.
  - An 8→6 override for Kamara/Tonali.

## D-019 · 2026-11-21 · Slice 2e: position templates, volume metrics, and the end of Phase 2
Evidence: `scripts/template_options.py` (per metric and peer group: ranked, zero share, median, p90), `scripts/check_2e.py` and `scouting report templates` on EPL 2025/26, run by the user. Option lists: `claude/TEMPLATE_OPTIONS.md` (the history of the picks; the TOML file is authoritative).
- **Templates (user's picks):** `backend/src/scouting/mappings/position_templates.toml`, one per peer role, in sections. GK 8 (goals prevented, saves; claims won, high claims, punches; sweeper actions; distribution accuracy, progressive passes). CB, FB, DM, CM, AM, W, ST 12 each (see the file). **W is deliberately attacking** (no out-of-possession metric; user). **ST keeps xGOT** (includes penalties: accepted) **and defensive actions** (accepted as long as it's consistent match to match). `report templates` on the real season: all 8 pass, every metric ranks more than half its group.
- **A. Volume next to success rates.** `*_completed` / `*_won` fractions ranked only the success rate (a winger 2/2 on dribbles outranked one 4/8). Five derived count metrics, ranked per 90: `passes_completed_count`, `crosses_completed_count`, `dribbles_completed_count`, `long_balls_completed_count`, `aerials_won_count`. **Derived at aggregation** from the parent's season row (registry `volume_of`; same successes and exposure minutes; never stored per match, so no renormalize and no migration). The parents were relabelled "Pass completion %", "Cross completion %", "Dribble success %", "Long-ball completion %", "Aerial duel success %". Example of why: Salah, dribble success 1st (lowest) of 63 Ws but successful dribbles per 90 27th of 65; crosses per 90 76th but cross completion 24th.
- **B. Near-duplicate pairs:** `dribbles_completed` (/players) over `take_ons_completed` (advanced), `chances_created` (/players) over `key_passes`: coverage when advanced analytics are missing (D-012) and FM's column names; eligibility nearly identical (W 63 vs 64).
- **C. Overall value models (VAEP, PV)** go on the player card next to the rating, not on templates (they contain the components already on the radar). VAEP/PV defensive splits aren't offered anywhere on templates: medians near zero or negative for most groups.
- **D. Template size:** 8–14 metrics (radars read best there), about 12 by default; keepers 8.
- **E. Passing minimum:** `passes_completed` min attempts 200 → 100 (it applies to every group; it mostly lets more strikers in: 26/33 before).
- **Outcome flag** in the registry (`OUTCOMES`): goals, assists, gca, second_assists, big_chances_missed, goals_conceded, errors_led_to_goal, own_goals, penalties_missed, penalties_saved. Kept off templates (D-015).
- **Data checks (`check_2e.py`):** **xGOT includes penalties** (20/20 single-shot scored penalties have it, mean 0.794; control 271/271 non-penalty). **Defensive actions** = tackles + interceptions + clearances + shot blocks + recoveries in 68.3% of 10,725 outfield appearances, otherwise about 1.09 lower on average (overlapping actions probably counted once; unverified). Both now in the metric descriptions.
- **Foundations for custom templates (built now; storage, API and edit UI in Phases 3–4):** one `Template` shape for built-in and user templates (`builtin` flag); `check_template` returns findings (errors: unknown metric, wrong position by `applies_to`, duplicate, empty section, unknown role; errors for built-ins but warnings for custom templates: outcome, near-duplicate pair, size outside 8–14); `check_coverage` warns when under half the group is ranked; `apply_template(PlayerSeason, Template)` gives rows per section (value, percentile, group size, model flag, direction, the reason when there's no percentile; flags a template for another position). Custom templates will live in a separate `user.db`, not in the rebuildable `scouting.db`. Whether "duplicate and edit a template" is in the MVP is decided in Phase 3–4 (estimate ~4–6 h then; foundations cost ~1–2 h now).
- **Format: TOML** (stdlib `tomllib`, comments allowed, no new dependency). The FM header mapping file (Phase 6) uses TOML too.
- **Close-out:** README rewritten (setup, env vars incl. `RAW_DB_PATH`, sync, aggregation, the 6/8 rule, templates, commands, when to re-run what); `inspect player` shows "6 or 8" after the "as a starter" breakdown; player lookup prefers an exact full or short name ("rodri" → Rodri). **Two full offline rebuilds gave identical checksums**; final state: groups GK 25 · CB 67 · FB 62 · DM 28 · CM 48 · AM 17 · W 65 · ST 33, **26,693 percentiles**, 100 metrics (95 ingested + 5 derived), 554 tests on the user's machine. **Phase 2 is done.**
- **Backlog:** cross-position templates (label which group each percentile is from, or rank everyone against one chosen group with `rank_against`, or show raw per-90s); weighted/composite scores (correlated metrics double-count, percentiles are ranks not amounts, ineligible metrics, false precision: prefer section weights, breakdown bars and rank order, on a later scouting-search page); possession-adjusted volume stats (defensive counts inflate for low-possession teams, passing counts for high-possession ones). Accented short names match only with the accent until Phase 3's search (fixed by the 3b search, D-020).
- **Rejected:**
  - YAML for templates (adds PyYAML and stubs for no gain over TOML).
  - Percentiles on two bases per fraction metric (changes the percentile key and every reader) and volume metrics re-ingested per match (needs a renormalize and duplicate `stat_value` rows).
  - VAEP/PV on templates; xAG, key passes and take-ons on templates.
  - Recoveries on the W template (the user wants an attacking profile).

## D-020 · 2026-11-22 · Phase 3: the read-only API (decisions, and slices 3a–3e as built) — PHASE 3 DONE
Design: `claude/PHASE3_API.md`. The user's "let's go ahead and go on 3a" (2026-11-22) was taken as **all six recommendations**; stated as an assumption when 3a was delivered, no objection since. Slice 3d's details were the user's own calls (below).
- **Decisions:**
  1. **Search: in-memory rapidfuzz index** per scope (no FTS5 table, no migration). Replaces the FTS5 plan in PLAN's stack table.
  2. **Public IDs = source IDs:** players by PitchAPI ID (`p_1reWwN`; FM players later get their own prefix), seasons by a scope key built from source IDs, `l_4WFCIZ.2025-2026` (a dot, since the slash in "2025/2026" would break URL paths). Survives a rebuild from an empty database.
  3. **Synchronous handlers** over the existing sync SQLAlchemy sessions.
  4. **Non-default `min_minutes` in Phase 3** (slice 3d): percentiles computed on the fly, nothing stored, responses say `stored: false`.
  5. **Custom templates not in Phase 3**; built-ins plus ad-hoc `metrics=` lists cover "compare on any metrics" until Phase 4 decides.
  6. **Contract tests in two layers:** endpoint tests on the fixture season (every error code, null handling, read-only checksum) + an **OpenAPI snapshot** (`tests/unit/api/openapi.json`; a schema change fails the test until regenerated with `UPDATE_OPENAPI=1`, so contract changes are deliberate; Phase 4 generates TypeScript types from it).
  - Compare is a `GET` with comma-separated players (bookmarkable).
- **Built in 3a (verified on EPL 2025/26):** FastAPI app factory, `/api/v1` prefix, `GET` only. **Read-only SQLite connection** (`sqlite:///file:/abs/path?mode=ro&uri=true`): the API physically can't write `scouting.db`. Schema checked at startup; an out-of-date schema doesn't stop the server, data endpoints answer 503 `SCHEMA_OUT_OF_DATE` and `/health` says why. Error envelope `{"error": {"code", "message"}}` (as PitchAPI's) for our errors, validation (400 `BAD_REQUEST`), unknown routes and methods. CORS: `localhost:5173` only. `scouting serve` binds to 127.0.0.1. Endpoints: `/health`, `/scopes`, `/scopes/{key}` (with coverage and peer groups), `/metrics`, `/metrics/{code}`, `/templates`, `/templates/{id}`.
- **Built in 3b (verified on EPL 2025/26):**
  - **`/players/search`:** names compared accent-free (dots and hyphens as spaces). Score tiers: exact name or short name **100**, surname **97**, initial + surname (first name starts with the initial, surname ratio ≥ 85) **95**, otherwise rapidfuzz WRatio over name, short name and surname, **capped at 94** so a fuzzy hit never ties an exact one; below **75** dropped; ties → more minutes, then name. Filters `team` (substring), `role`, `min_minutes`, `limit` (≤ 50). The index is cached per scope and rebuilt when the scope's `computed_at` changes. This fixed the accented short-name gap (D-019).
  - **`/players/{id}`** (card): profile, provenance, minutes/matches/starts, teams, starts by role, primary/peer role and its source, **6/8 block** for central mids (group, method, depth and partner gap in metres, starts behind each, borderline, the rule's constants), rating + rated matches, peer group (`size`, `min_minutes`, `small_sample` < 20, `stored`), `not_ranked_reason`, coverage (his matches without advanced data / without a heatmap).
  - **`/players/{id}/stats`:** one line per metric: total, attempts, per 90, rate, `rate_scale`, exposure minutes, matches, percentile, `ranked_among`, `reason` when there's no percentile. `metrics=a,b` keeps the order asked; codes he has no line for (outfield metrics for a keeper) are listed in **`missing`**, not silently dropped.
  - **`/players/{id}/matches?metric=`:** every appearance, oldest first, with opponent, venue and the score from his team's side; **no data = `null`** (the appearance stays, so a trend line shows the gap). A volume metric reads its parent's per-match successes (`stored_metric`).
  - **`rate_scale`** (decided while building 3b): success rates are a **0–1 share**; averaged keeper rates (`value_is_rate`) keep their unit, **percent** (0–100) or **metres** (`avg_pass_length`). For averaged rates **`total` and the per-match `value` are `null`** and only `rate` is filled, because the stored value is rate × weight (e.g. 4,800), not a meaningful total.
  - **Errors:** `PLAYER_NOT_FOUND` 404, `PLAYER_NOT_IN_SCOPE` 404, `SCOPE_NOT_FOUND` 404, `SCOPE_NOT_AGGREGATED` **409** (listed in OpenAPI only on routes that need season stats), `UNKNOWN_METRIC` 404, `UNKNOWN_ROLE` 400.
  - **Verified (user, 2026-11-22):** the five test searches resolve first ("salah" 97, "odegard" → Ødegaard 93.3, "E. Haaland" 100, "rodri" 100, "g. rodriguez" 100). Salah's card agrees with the Phase 2 values (2,148 min, 27 matches, 23 starts, W, group 65 at 855 min); stats xG 8.16 (0.342/90, 92nd of 65 W), pass completion 610/793 (28th); first match 15 Aug 2025 v Bournemouth, home, 4–2.
- **Built in 3c (verified on EPL 2025/26):**
  - **`/players/{id}/template?template=`** (radar data): `template` = id (`builtin:W`) or a role shortcut (`W`); **default: the template of his own peer role**. Rows are the same `StatLineOut` lines as `/stats` (a test checks they're identical); a metric he has no data for comes back with minutes 0, nulls and "no data". A template for another position is still ranked in his own group, flagged `same_group: false`. Errors `UNKNOWN_TEMPLATE` 404, `TEMPLATE_REQUIRED` 400 (no peer role and no `template=`).
  - **`/groups/{role}?metrics=&sort=`** (rankings, bars, scatter data): every member of the stored group (the role, at least the group's minutes; players under the threshold aren't members), each with value (per 90 or rate), total, attempts, minutes, percentile and reason per metric; a header per metric (basis, `rate_scale`, model, direction, ranked among). Default metrics = the role's template; **at most 14** (the largest template size; the proposal said 12). Sorted best first on `sort` (default the first metric; lower-is-better ascending; no value last; then name). **The API doesn't pick scatter labels**: the UI chooses from all rows. Error `GROUP_NOT_FOUND` 404.
  - Shared response helpers moved to `api/mapping.py`; `metrics.templates.stat_reason` (the per-line eligibility reason) is reused by `/groups`.
  - **Verified (user):** Salah's W radar through the API = `inspect player --template` on all 12 metrics (npxG 88.5, shots 93.1, box touches 94.6, xA 86.9, chances created 86.9, passes into box 96.2, crosses/90 76.2, cross completion 24.2 of 60, dribbles/90 26.9, dribble success 0.79 of 63, progressive carries 68.5, carries into box 79.2); `/groups/W?metrics=npxg,xa` top 10 = `report peers` in order and value (Brooks 0.4376, 99.2 … Okafor 0.3019, 85.4); `template=ST` → `same_group: False`, ranked in W.
- **Slice 3d: the user's calls (2026-11-22):**
  - **A. Default metrics in `/compare` = the first listed player's position template** (user: "the user can always change to the second player's if desired once UI is implemented: better than requiring or error"). Claude had moved its recommendation to "the shared position's template, otherwise an error" because the first-player rule makes the order matter; the user chose first-player.
  - **B. A player outside the group he's ranked against is ranked against the group's members only, never added** (user: "I don't want statistics being skewed if say, eventually a FM player is added with bananas stats"). This was chosen over "members plus himself" (his exact would-be percentile). Rates still need the metric's minimum attempts (so 2/2 dribbles can't rank 100th).
  - **C. `min_minutes` on `/stats`, `/template`, `/groups` and `/compare`.**
  - **D. Lowest `min_minutes` = 90** (user: "most people won't be ranking players who have less than one full game played").
- **Built in 3d (verified on EPL 2025/26):**
  - **`metrics.percentiles.live_group(session, scope, role, min_minutes, codes)`** → `LiveGroup` (members at that threshold, each metric's sorted eligible values, `rank(code, value)` = midpoint). Same eligibility (`eligible_value`, now public) and formula as the stored percentiles; nothing written. **A test checks that at the stored threshold it reproduces every stored percentile and the eligible count per metric.** `rank_against` (stored populations only) is no longer used by the API.
  - **`api/ranking.py`:** `custom_threshold` (None = use the stored numbers; also when the value equals the stored threshold), `season_at` (a season re-ranked at another threshold; under it → no group, "not ranked: below M min"), `rank_in` (one metric against a group, member or outsider).
  - **`/compare?players=a,b[,…]&metrics=|template=&against=&min_minutes=`:** 2–6 distinct players; each ranked in his own group, or all `against=ROLE`; per player the group he's ranked in (size at the threshold) and **`outside_group`**; per metric value, total, attempts, minutes, percentile, ranked among (members only) and reason; `template` echoed (null with `metrics=`), `min_minutes`, `stored`. Errors: player count or duplicates, `metrics` and `template` together (400), `UNKNOWN_ROLE`, `TEMPLATE_REQUIRED` (first player has no role and no metrics/template), `UNKNOWN_TEMPLATE`, `UNKNOWN_METRIC`, player errors, 409. A role with nobody at the threshold → group null, reason "no GK players with at least M min".
  - **`min_minutes`** (≥ 90; validation → 400) on `/stats`, `/template`, `/groups`: `group.stored: false`, the group's size at that threshold.
  - **Claude's calls, beyond the decisions:** an outsider needs at least 90 minutes of exposure on a per-90 metric (the user's one-full-match rule), capped at the group's own threshold so he never needs more than its members; on a season in progress whose stored threshold is under 90, that exact value can't be passed explicitly (omitting `min_minutes` uses it).
  - **Verified (user):** two wingers and a striker `against=W`: Salah and Mbeumo members of W 65 with their stored numbers (Salah 88.5 / 93.1 / 94.6 / 86.9; Mbeumo npxG 97.7); Haaland `ST → W 65 outside`, npxG 100, shots 100, box touches 89.2, xA 16.9 (ranked among 65). Haaland listed first → ST template, each in his own group (ST 33, W 65). `min_minutes=600`: W group 72, `stored: false`, same top five in order (Brooks 99.3 …); Salah's npxG 88.2 of 72.
- **3e (close-out):** the OpenAPI document no longer lists a 422 on every route (FastAPI's default) and drops the unused `HTTPValidationError` schemas, since validation errors are answered as 400 `BAD_REQUEST` (user's choice, so Phase 4's generated TypeScript types match what the API sends); a test guards it. The repo README gained a short "Using the API" section that points to `/docs` for the full reference (user: short section + `/docs`, so it can't drift from the code). **Exit criterion met: the API contract tests pass** (633 tests on the user's machine expected after 3e). **Phase 3 is done.**
- **Layering held:** `api` imports only `db`, `metrics`, `domain`, `config`; `scouting serve` loads the app by import string, so `cli` doesn't import `api`.
- **Backlog:** search scoring (user: "we can sort out search scoring later"): short fuzzy noise below the right player (José Sá 90 for "salah"; E. Rowe / E. Eze 85.5 for "E. Haaland"), from WRatio's partial matching of short names.
- **Rejected:** SQLite FTS5 (migration, raw SQL, a rebuild step; pays off only at hundreds of thousands of names); internal integer IDs in URLs (change on a rebuild); async handlers with aiosqlite (duplicate query layer for no gain); custom-template storage now; endpoint tests without a snapshot; requiring `metrics=`/`template=` in `/compare`, or erroring on mixed positions; ranking an outsider against the members plus himself; ranking outsiders on rates without the attempt minimums; `min_minutes` below 90; a full hand-written API reference in the README.