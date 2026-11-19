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
