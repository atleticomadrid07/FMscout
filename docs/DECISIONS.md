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