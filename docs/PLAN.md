# PLAN.md: Dual Soccer + FM Scouting Tool

Last updated: 2026-11-21 (Phase 2 done). Decisions and the reasoning behind them are in [DECISIONS.md](../DECISIONS.md). The approved data model is in [SCHEMA.md](SCHEMA.md).

## Scope

- **Primary target:** real-world scouting. FM scouting comes after the MVP and builds on the same core.
- **MVP:** real-world data only. Top 5 leagues (EPL, La Liga, Bundesliga, Serie A, Ligue 1). The crawl starts with EPL only, then adds the others.
- **After the MVP, more leagues:** Eredivisie, Liga Portugal, MLS, Saudi Pro League, and others later. Check coverage per league first, because some leagues have no ratings or momentum.
- **Data:** post-match only. No live data and no `/live` route.
- **FM:** FM24 `.rtf` exports are the supported route. The `.fm` save file is a stretch goal and starts with research only.
- **Hosting:** undecided; local-only for now. A public site needs PitchAPI's terms of use checked first (D-007).

## Stack (decided)

| Layer | Choice |
|---|---|
| Backend | Python 3.12, uv, FastAPI (Phase 3), Pydantic v2 |
| Config | pydantic-settings, reading the repo-root `.env` (`PITCHAPI_API_KEY`, `DATABASE_URL`, `RAW_DB_PATH`, `SYNC_MAX_RPS`, `LOG_LEVEL`) |
| Database | SQLite with SQLAlchemy 2.x + Alembic (schema stays portable; migrations in `backend/migrations/`, auto-formatted by ruff; string server defaults only, D-013); `raw.db` uses stdlib sqlite3 |
| Stats | SQL aggregation + plain Python for percentiles (pandas not needed so far) |
| Config files | TOML via stdlib `tomllib` (position templates; FM header map in Phase 6) (D-019) |
| Search | SQLite FTS5 + rapidfuzz + unidecode (Phase 3; name-fragment lookup with exact-name preference until then) |
| Sync jobs | Typer CLI `scouting` (`uv run scouting --help`), runs separately from the web app |
| PitchAPI client | Our own httpx client returning raw JSON (D-009), wrapped by our own cache layer (D-004) |
| Frontend | Vite + React + strict TypeScript, TanStack Query |
| Charts | Decided at Phase 4. Candidates: ECharts, Plotly, Recharts. Pitch visuals will probably be custom SVG/D3 |
| Tests | pytest + hypothesis; Vitest |
| Lint and types | ruff, mypy --strict; ESLint, Prettier, tsc strict |

## Guidelines

1. A phase is done when its exit criteria are met and the tests pass.
2. Data before UI. No chart is built until its numbers are tested against values worked out by hand from the fixtures.
3. The canonical model is FM-aware from day one: internal surrogate IDs, with external IDs mapped for each source.
4. The MVP scope is frozen. New ideas go into the Backlog section below.
5. Raw API responses are cached, so data can be re-normalized without re-crawling.
6. Crawl scope grows one league at a time.
7. The web app never calls PitchAPI. Only the sync CLI does.

## Phases

| # | Phase | Exit criteria | Est. h | Status |
|---|---|---|---|---|
| 0 | Spike + foundations: real requests, fixtures, coverage matrix, terms-of-use check, repo/tooling, schema draft | Endpoints and fields confirmed; canonical model approved | 6–10 | **Done 2026-11-19.** Carried over: the terms-of-use email to PitchAPI (blocks hosting only, D-007) |
| 1 | Ingestion slice: EPL, one complete season → raw cache → canonical tables, upserts, resumable | Running twice gives an identical DB; resume works after a kill | 15–25 | **Done 2026-11-20.** EPL 2025/26 fully loaded; exit checks below all passed |
| 2 | Aggregation + metrics engine: registry, per-90, null handling, position assignment, peer groups, percentiles, 6/8 split, position templates | Hand-checked per-90 values match; table-driven tests pass | 15–25 (+ 2d, 2e) | **Done 2026-11-21.** Two offline rebuilds give identical checksums; 26,693 percentiles across 8 groups; 554 tests |
| 3 | Backend API: search, player, player-season, compare, peers, templates; provenance in every response | API contract tests pass | 6–10 | **Next** (design first) |
| 4 | MVP UI: search, player page, radar, bars, comparison with position presets, scatter | **MVP done** | 25–40 | |
| 5 | Breadth: EPL backfill to 2021, rest of the top 5, incremental sync, coverage badges, team pages | | 10–20 | |
| 6 | FM24 `.rtf` import: header detection, mapping file, snapshots, FM-vs-FM comparison | | 20–30 | |
| 7 | FM ↔ real-world comparability layer | | 15–25 | |
| 8 | Rich visuals: shot maps, heatmaps, pass networks, form and trends | | 25–40 | |
| 9 | Polish / design system | | 20+ | |
| — | Stretch: research the `.fm` save format | | 4–8 | |

Weekly hours vary and there's no target date. Track progress by phase, not by calendar date. Totals: MVP is about 67–110 h; through Phase 7 is about 112–185 h.

### Phase 1 slices (done)

| Slice | Builds | Status |
|---|---|---|
| 1a | Settings, `raw.db` store, `CachedFetcher` with freshness policies and negative cache, offline mode | **Done** 2026-11-19 |
| 1b | SQLAlchemy base + Alembic; competition, season, team, match + source-ID maps, sync_state; leagues and match-list parsers; `scouting sync matches` | **Done** 2026-11-20 (380 matches, 20 teams, 2 requests) |
| 1c | Players/advanced/lineups parsers; player, appearance, formation_role, registry v0, stat_value; `applies_to` + goalkeeper rule; `sync match`, `inspect appearance` | **Done** 2026-11-20 (hand check passed) |
| 1d | `sync season` (resumable, `--limit`, `--renormalize`, Ctrl-C safe), profiles, full registry (95 metrics, D-012/D-013), 19 formation mappings, `report formations`, `report checksum` | **Done** 2026-11-20 |

**Phase 1 exit checks (EPL 2025/26, all passed 2026-11-20):** 380/380 matches, 1,679 cached responses, 849,359 stat values, 537 profiles; resume after Ctrl-C worked; `report checksum` identical across re-runs; every starter has a role.

### Phase 2 slices (done; design D-014 to D-019)

| Slice | Builds | Status |
|---|---|---|
| 2a | Tables `scope`, `player_scope`, `player_scope_team`, `player_scope_role`, `player_scope_stat`; totals, exposure minutes, per-90s, rates, primary role; `scouting aggregate`; `inspect player` | **Done** 2026-11-20: Salah goals 7 / 2,148 min = 0.2933, van Dijk clearances 275 / 3,420 min = 7.237, Alisson's weighted distribution 80.99% over 936 |
| 2b | `peer_group` + `percentile` tables, `metric.min_attempts` + `metric.direction`; midpoint percentiles; small-group warning; `rank_against`; `report peers`, `report thresholds` | **Done** 2026-11-20: 24,893 percentiles across 7 groups |
| 2c | Data checks (`db/checks.py`, `scripts/check_2c.py`) and the resulting changes (D-016) | **Done** 2026-11-20: `was_fouled` NULL = 0; 3-4-2-1 84/86 → W; xA for templates |
| 2d | **6s and 8s as separate peer groups** (D-017, D-018): heatmaps fetched, per-appearance depth, `report depth`, classification in `aggregate` (CB reference, 14 m cut, deeper partner ≥ 2 m, single-pivot fallback), DM peer group, migration dropping/recreating the derived tables | **Done** 2026-11-21: GK 25 · CB 67 · FB 62 · DM 28 · CM 48 · AM 17 · W 65 · ST 33; all 14 anchors on the right side |
| 2e | Position templates (D-019): `scripts/template_options.py` + option lists; 5 derived volume metrics, outcome flag, pass minimum 100, `scripts/check_2e.py`; `mappings/position_templates.toml` (8 templates, user's picks); `metrics/templates.py` (Template shape, findings, coverage check, apply to a player); `inspect player --template`, `report templates` | **Done** 2026-11-21: all templates pass on the real season; 26,693 percentiles |
| close-out | README rewrite; "6 or 8" line after the starts breakdown; exact-name player lookup; two full offline rebuilds | **Done** 2026-11-21: checksums identical |

## Crawl order

1. ~~EPL, one complete season (2025-26), as the Phase 1 slice.~~ Done.
2. EPL backfill to 2021-22 (verified available for all top-5 leagues).
3. EPL current season, incremental.
4. La Liga, then Bundesliga, Serie A, Ligue 1.
5. After the MVP: Eredivisie, Liga Portugal, MLS, Saudi Pro League, and others, each after a coverage check.

Load (measured on EPL 2025/26): 2 list requests + **4 per match** (players, advanced, lineups, heatmaps) + first-time profiles ≈ 2,060 requests for a first EPL season; later seasons reuse most profiles. At 1 request/s that's about 35 minutes per league-season. Run long syncs with `LOG_LEVEL=ERROR` for a quiet terminal. A full offline rebuild of a season takes about a minute.

## FM workflow

1. ~~At the end of Phase 2 (slice 2e), produce the list of PitchAPI metrics the templates use, with the FM equivalents we'd like.~~ The metric list is fixed (the template file); the FM wishlist is written when Phase 6 starts, from that list.
2. The user builds an FM24 player view with the available FM columns closest to that list and exports `.rtf`.
3. The FM side of the mapping is based on that real file, never on a guessed format. The mapping file is TOML (D-019).

## Open questions

Verified details are in [PITCHAPI_NOTES.md](PITCHAPI_NOTES.md).

**Answered:**
- **Season format:** `"2025/2026"`, taken from `/v1/leagues`.
- **Raw JSON / fetch choice:** our own httpx client (D-009).
- **Per-match position (spike):** lineups slot grid code + formation → role; profile `position_id` is a fallback only.
- **`chances_created` (spike):** the advanced one equals `key_passes` in 277/277 rows. The `/players` one is stored separately as an alternative definition (D-012); templates use the `/players` one (D-019).
- **Minutes denominator:** `/players` minutes, a full match = 90 (D-010).
- **Missing keys in `/players` (spike):** the meaning depends on the key; each metric gets an absence policy (see PITCHAPI_NOTES).
- **Schema questions:** all resolved (D-011).
- **Metric list and overlap sources:** D-012, D-013.
- **Every formation in EPL 2025/26:** 19, all mapped (D-013). Other leagues may add more; `report formations` shows them.
- **Penalty/own-goal keys:** sent only when ≥ 1, so missing = 0; own goals aren't in `goals` (crawl, PITCHAPI_NOTES).
- **Aggregation and percentile rules:** D-014. **Template principle:** D-015.
- **2c (D-016):** `was_fouled` NULL = 0; `launch_pct` and `claim_rate` are their counts' ratios; 3-4-2-1's 84/86 are W; xA (verified real xA) over xAG.
- **Heatmaps (2d spike, D-017):** per player, one request per match, frame `acting_ltr`.
- **6/8 reference point, cut and partner gap (D-018):** centre-back line, 14 m, G = 2 m.
- **Templates and near-duplicate pairs (D-019):** dribbles (/players), chances created (/players); xGOT includes penalties; defensive actions ≈ tackles + interceptions + clearances + blocks + recoveries (68% exact).

**Still open:**
- **Phase 3 design:** endpoints, response shapes (provenance in every response), how the API exposes templates and the "rank against a group" path, whether custom templates ("duplicate and edit") are in the MVP.
- **The 6/8 rule outside EPL 2025/26:** calibrated on one league-season. Re-run `report depth` per league/season in Phase 5 before trusting the split there.
- **6/8 for FM players (Phase 6):** FM scopes have no heatmaps; the split will need FM's own position/role.
- **Heatmap coverage outside EPL 2025/26** (other leagues, older seasons): seen when those are crawled; a missing heatmap falls back to formations.
- **Late analytics.** Can advanced analytics appear later for a match that first returned `ANALYTICS_UNAVAILABLE`? None in EPL 2025/26; watch other leagues.
- **Terms of use for caching and redistribution.** Not in the docs. Ask PitchAPI support. Must be answered before hosting.
- **Penalty goals:** PitchAPI gives none, so non-penalty goals can't be computed; the page compares goals with xG instead (D-015).
- **Per-league data meanings:** once more leagues are in (Phase 5), re-run `scripts/check_2c.py` and `scripts/check_2e.py` per league; a NULL convention or rate definition may differ by league. Re-run `report templates` too.

**Known cosmetic issue:** the progress line's time estimate is too low early on when part of the work comes from cache (not worth fixing now).

## Backlog (post-MVP ideas)

- **Pooled peer groups** (Phase 5): one peer group per role across the top 5 leagues, or across several seasons of one league, for larger samples (D-014). Needed for AM (17 in EPL 2025/26) and helps the DM/CM split.
- **Phase 4 UI:** the depth/6-or-8 display next to a player's label must be removable/toggleable (D-018).
- **Custom templates** (if not in the MVP): storage in `user.db`, API, edit form; the foundations exist (D-019).
- **Cross-position templates** and **weighted/composite scores** (design notes in D-019).
- **Possession-adjusted volume stats** (D-019).