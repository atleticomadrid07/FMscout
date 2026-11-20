# MEMORY.md

Session-to-session log (see CLAUDE.md §11). Read this at the start of every session. Update it after significant decisions and at "session end".

## Current state

- **Phase:** 0, spike + foundations
- **In progress:** project structure done; spike next
- **Next up:** `backend/pyproject.toml` (uv, ruff, mypy, pytest) and `backend/scripts/spike_pitchapi.py`. The user runs the spike locally, then we finalize the canonical schema against the real fields

## Session log

### 2026-11-17 / 2026-11-18: Planning
- **Worked on:** project plan, stack, scope, caching strategy.
- **Decided:** stack B (FastAPI + React); SQLite + Alembic; MVP = top 5 leagues, crawl starts with EPL; the local DB is the app's only read path; post-match data only; FM24 `.rtf` as the primary FM import; hosting later, gated on a terms-of-use check. See DECISIONS.md D-001 to D-007.
- **Rejected:** Streamlit (UI ceiling), all-TypeScript (weaker stats tooling), calling the API on demand.

### 2026-11-19: Repo setup
- **Added:** README, .gitignore, .gitattributes, .editorconfig, .env.example, DECISIONS.md, docs/PLAN.md, CLAUDE.md, MEMORY.md, ERRORS.md.
- **Not started:** any code.

### 2026-11-19: Docs review + project structure
- **Read:** the official PitchAPI docs (PDF) and the SDK 0.1.6 source. Findings are in docs/PITCHAPI_NOTES.md.
- **Key findings:**
  - Season strings look like `"2025/2026"`.
  - SDK models default missing counts to 0, so we parse raw JSON ourselves.
  - Position is per-match via lineups; profile `position_id` numbers are undocumented.
  - The docs contradict themselves on `chances_created`.
  - `/live` does exist, but it's not used (D-005).
  - The docs include no terms of use.
- **Decided:** D-008, the repo structure (`src/` layout, layered packages).
- **Added:** the backend folder skeleton, docs/ARCHITECTURE.md, docs/PITCHAPI_NOTES.md.
