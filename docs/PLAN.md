# PLAN.md: Dual Soccer + FM Scouting Tool

Last updated: 2026-11-19. Decisions and the reasoning behind them are in [DECISIONS.md](../DECISIONS.md). The approved data model is in [SCHEMA.md](SCHEMA.md).

## Scope

- **Primary target:** real-world scouting. FM scouting comes after the MVP and builds on the same core.
- **MVP:** real-world data only. Top 5 leagues (EPL, La Liga, Bundesliga, Serie A, Ligue 1). The crawl starts with EPL only, then adds the others.
- **After the MVP, more leagues:** Eredivisie, Liga Portugal, MLS, Saudi Pro League, and others later. Check coverage per league first, because some leagues have no ratings or momentum.
- **Data:** post-match only. No live data and no `/live` route.
- **FM:** FM24 `.rtf` exports are the supported route. The `.fm` save file is a stretch goal and starts with research only.
- **Hosting:** eventually, on the user's existing website. Check PitchAPI's terms of use before deploying.

## Stack (decided)

| Layer | Choice |
|---|---|
| Backend | Python 3.12, uv, FastAPI, Pydantic v2 |
| Database | SQLite with SQLAlchemy 2.0 + Alembic (schema stays portable) |
| Stats | pandas |
| Search | SQLite FTS5 + rapidfuzz + unidecode |
| Sync jobs | Typer CLI that runs separately from the web app |
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
| 1 | Ingestion slice: EPL, one complete season → raw cache → canonical tables, upserts, resumable | Running twice gives an identical DB; resume works after a kill | 15–25 | Next: slice plan proposed, awaiting go-ahead |
| 2 | Aggregation + metrics engine: registry, per-90, null handling, position assignment, peer groups, percentiles | Hand-checked per-90 values match; table-driven tests pass | 15–25 | |
| 3 | Backend API: search, player, player-season, compare, peers; provenance in every response | API contract tests pass | 6–10 | |
| 4 | MVP UI: search, player page, radar, bars, comparison with position presets, scatter | **MVP done** | 25–40 | |
| 5 | Breadth: EPL backfill to 2021, rest of the top 5, incremental sync, coverage badges, team pages | | 10–20 | |
| 6 | FM24 `.rtf` import: header detection, mapping file, snapshots, FM-vs-FM comparison | | 20–30 | |
| 7 | FM ↔ real-world comparability layer | | 15–25 | |
| 8 | Rich visuals: shot maps, heatmaps, pass networks, form and trends | | 25–40 | |
| 9 | Polish / design system | | 20+ | |
| — | Stretch: research the `.fm` save format | | 4–8 | |

Weekly hours vary and there's no target date. Track progress by phase, not by calendar date. Totals: MVP is about 67–110 h; through Phase 7 is about 112–185 h.

## Crawl order

1. EPL, one complete season (2025-26), as the Phase 1 slice.
2. EPL backfill to 2021-22 (verified available for all top-5 leagues).
3. EPL current season, incremental.
4. La Liga, then Bundesliga, Serie A, Ligue 1.
5. After the MVP: Eredivisie, Liga Portugal, MLS, Saudi Pro League, and others, each after a coverage check.

Load (spike-verified): 1 unpaginated match-list request + 3 requests per match (players, advanced, lineups) + first-time player profiles ≈ 1,150 + ~600 profiles per EPL season. At 1 request/s that's about 30 minutes per league-season.

## FM workflow

1. At the end of Phase 2, produce the list of PitchAPI metric keys we use, with the FM equivalents we'd like.
2. The user builds an FM24 player view with the available FM columns closest to that list and exports `.rtf`.
3. The FM side of the mapping is based on that real file, never on a guessed format.

## Open questions

Verified details are in [PITCHAPI_NOTES.md](PITCHAPI_NOTES.md).

**Answered:**
- **Season format:** `"2025/2026"`, taken from `/v1/leagues`.
- **Raw JSON / fetch choice:** our own httpx client (D-009).
- **Per-match position (spike):** lineups slot grid code + formation → role; profile `position_id` is a fallback only.
- **`chances_created` (spike):** equals `key_passes` in 277/277 rows.
- **Minutes denominator:** `/players` minutes, a full match = 90 (D-010).
- **Missing keys in `/players` (spike):** the meaning depends on the key; each metric gets an absence policy (see PITCHAPI_NOTES).
- **Schema questions:** all resolved (D-011).

**Still open:**
- **Every formation PitchAPI uses.** Collect them in the Phase 1 crawl, then complete `formation_role` before Phase 2.
- **Role judgement calls** (4-2-3-1's 64/66 as DM; 3-4-2-1's 84/86 as AM): check against real players in Phase 2.
- **Late analytics.** Can advanced analytics appear later for a match that first returned `ANALYTICS_UNAVAILABLE`? Observe over a few weeks.
- **`was_fouled` null.** Does it mean 0? Check in Phase 1 against the opponents' `fouls`.
- **Terms of use for caching and redistribution.** Not in the docs. Ask PitchAPI support. Must be answered before hosting.

## Backlog (post-MVP ideas)

- (empty)