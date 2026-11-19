# PLAN.md: Dual Soccer + FM Scouting Tool

Last updated: 2026-11-18. Decisions and the reasoning behind them are in [DECISIONS.md](../DECISIONS.md).

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
| PitchAPI client | Official Python SDK, wrapped by our own cache layer (see D-004) |
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
| 0 | Spike + foundations: real requests, fixtures, coverage matrix, terms-of-use check, repo/tooling, schema draft | Endpoints and fields confirmed; canonical model approved | 6–10 | In progress (design) |
| 1 | Ingestion slice: EPL, one complete season → raw cache → canonical tables, upserts, resumable | Running twice gives an identical DB; resume works after a kill | 15–25 | |
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
2. EPL backfill to the earliest season available (2021-22 according to coverage notes; verify).
3. EPL current season, incremental.
4. La Liga, then Bundesliga, Serie A, Ligue 1.
5. After the MVP: Eredivisie, Liga Portugal, MLS, Saudi Pro League, and others, each after a coverage check.

Rough load: about 380 matches × 2 match endpoints, plus first-time player profile lookups, comes to about 1,000–1,300 requests per EPL season. At 1 request/s that's well under an hour per season.

## FM workflow

1. At the end of Phase 2, produce the list of PitchAPI metric keys we use, with the FM equivalents we'd like.
2. The user builds an FM24 player view with the available FM columns closest to that list and exports `.rtf`.
3. The FM side of the mapping is based on that real file, never on a guessed format.

## Open questions

- Does the match player stats response include per-match position and player name? This decides the position rule and how often we need player profile lookups. Answered in Phase 0.
- Does the SDK expose raw JSON payloads, so we can cache them? If not, the fetch layer uses httpx directly. Answered in Phase 0.
- Can advanced analytics appear later for a match that first returned `ANALYTICS_UNAVAILABLE`? This decides how often we re-check those matches. Observe over a few weeks.
- PitchAPI terms of use for caching and redistribution. Must be answered before hosting.

## Backlog (post-MVP ideas)

- (empty)
