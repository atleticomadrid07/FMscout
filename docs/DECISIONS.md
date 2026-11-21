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
  - Picking one of xA/xAG now: the user wants to decide when building the radars.
## D-013 · 2026-11-20 · After the EPL 2025/26 crawl: 11 more formations, 5 more metrics
- **Formations:** `report formations` found 19 formations in the season; the 11 not in the spike now have explicit mappings (SCHEMA.md §7). The user's judgement calls:
  - **Slot 95** (behind a lone striker in 4-4-1-1, 3-5-1-1, 5-3-1-1): **AM**.
  - **4-1-3-2's 83/87:** **CM** (a narrow diamond).
  - **4-3-2-1's 84/86:** **AM**, as in 3-4-2-1.
  - **5-4-1's 72/78:** **W**, following D-011 (wide midfielders go with wingers).
- **Metrics (95 total):** `/players` keys not seen in the spike's match, all added as counts: `penalties_won`, `penalties_conceded` (all players), `penalties_missed`, `own_goals` (all players), `penalties_saved` (keepers only). "Missing means 0" confirmed on all 15,193 rows of the season (PITCHAPI_NOTES).
- **Process fixes:** the profile step commits every 25 players, prints progress, handles Ctrl-C and skips a malformed profile; per-match warnings moved to DEBUG because the CLI summary already reports them once with counts.
- **SQLite migration rule:** use plain string server defaults (`server_default="0"`), never `sa.text(...)` or `false()`. Alembic rebuilds the whole table for a SQL-expression default, and with foreign keys on, that fails once other tables reference it.
- **Rejected:** slot 95 as ST, 4-1-3-2's 83/87 as W.
## D-014 · 2026-11-20 · Phase 2 design: aggregation and percentile rules
- **Aggregation (slice 2a, built and hand-checked on EPL 2025/26):**
  - **Exposure minutes per metric** = minutes of the appearances with a row for that metric. Matches without advanced analytics add no minutes to advanced metrics; keepers' minutes never count for shots.
  - **A stored NULL** follows the registry's `season_null`: `skip` (unknown: adds no total and no minutes; `was_fouled`, the default) or `zero` (a known zero: `xgot`, NULL = no shot on target).
  - **per90** = total ÷ exposure minutes × 90; **rate** = Σ successes ÷ Σ attempts (the exact weighted average for keeper rates). NULL when undefined.
  - **Primary role** = most minutes as a starter (ties: more starts, then role order). No starts: the profile's listed slot, only where every mapped formation agrees on it (`role_source = 'listed'`); otherwise no role.
  - **Rebuilt per season in one transaction**, deterministic (`computed_at` = latest `normalized_at`), so `report checksum` stays stable. Runs automatically after a `sync season` that leaves nothing pending, and on demand with `scouting aggregate`.
- **Percentiles (slice 2b):**
  - **Formula: midpoint.** pct = (players below + ½ × players tied) ÷ n × 100. Tied players (mostly zeros in rare-event stats) sit in the middle of their block, not at its bottom or top.
  - **Eligibility per metric:** per-90 metrics need the minimum minutes (855 for a full EPL season) on that metric's own exposure; rate metrics need a minimum number of season attempts. Approved starting values (tune with `report thresholds`): passes 200, long balls 20, crosses 20, dribbles 15, take-ons 15, ground duels 30, aerials 15, claims 10 (`claims_won`, `claim_rate`), distributions 100 (`distribution_accuracy`, `launch_pct`, `avg_pass_length`). No minimums on count totals: cutting off low performers would skew everyone else's percentiles.
  - **Stored in the `metric` table:** two new columns, the minimum attempts and a direction (higher / lower / none), so the API and UI can explain an exclusion ("needs 200 passes"). Safe migration: added columns with string defaults (D-013 rule).
  - **Excluded players get no percentile row**; the UI derives the reason from the season stats. They can still be compared: the on-the-fly "rank against a group" function places them against the peer group without adding them to it, flagged.
  - **Each percentile stores the number of players it was ranked among**, since eligibility can differ per metric within one peer group.
  - **Stored raw:** low value = low percentile for every metric. The UI flips lower-is-better metrics. "No direction" (not coloured good or bad): `launch_pct`, `avg_pass_length`, `corners`.
  - **Small peer groups:** warn below 20 players, but still show the percentiles. EPL 2025/26 has none (smallest: GK 25, AM 25).
  - **Bases:** per-90 for counts, model outputs and distances; rate for fractions. No percentiles on season totals (they mostly measure minutes).
- **Why:** Matches the scouting requirements (explicit peer group, sample size always shown, null ≠ 0), and keeps the UI from scanning `stat_value`.
- **Rejected:**
  - `percent_rank` (puts all tied zeros at 0th) and cumulative distribution (puts them at the top of their block, so "did nothing" can look above average).
  - Statistical shrinkage for small samples: more accurate, but overkill for now.
  - Storing percentiles pre-flipped: the user prefers the UI to flip.
  - Thresholds in code only: the UI couldn't explain an exclusion.
  - Larger peer groups by pooling leagues or seasons: adding leagues doesn't enlarge a one-league peer group by itself. Pooling the top 5 is a Phase 5 option (backlog).
## D-015 · 2026-11-20 · Position templates measure process, not outcomes (slice 2d)
- **Decision:** one template per peer role (GK, CB, FB, CM, AM, W, ST), about 12 metrics in sections, in `mappings/position_templates.yaml`. They drive the radars and the comparison presets. Templates use **underlying / process metrics**: npxG, xGOT, shots, touches in the box, xA or xAG, key passes or chances created, SCA, xT, VAEP, progressive actions, defensive actions and duel success; for keepers, goals prevented, claims and distribution. **Outcomes stay out of templates** (goals, assists, GCA, big chances missed, goals conceded) and are shown prominently elsewhere on the scouting page.
- **Why:** the user's scouting view: two strikers with 10 npxG are getting into the same positions whether they scored 5 or 11; finishing varies. Templates also fix the metric list the product uses, which becomes the FM24 view's column list (D-006), and they settle the near-duplicate pairs (`xa`/`xag`, `dribbles_completed`/`take_ons_completed`, `claims_won`/`claim_rate`, `key_passes`/`chances_created`).
- **Caveats:** model outputs are flagged as models in the UI. PitchAPI gives no penalty goals, so "goals vs npxG" would favour penalty takers; the fair finishing comparison on the page is **goals vs xG** (both include penalties).
- **Rejected:** leaving templates to Phase 4: the metric list is needed for the FM workflow at the end of Phase 2, and the templates can be checked against real eligibility counts now.
 