# MEMORY.md

Session-to-session log (see CLAUDE.md §11). Read this at the start of every session. Update it after significant decisions and at "session end".

## Current state

- **Phase:** 0, spike + foundations
- **In progress:** repo setup
- **Next up:** repo scaffold (uv, ruff/mypy, frontend skeleton), plus a PitchAPI spike script run locally to collect fixtures; then finalize the canonical schema against the real fields

## Session log

### 2026-11-17 / 2026-11-18: Planning
- **Worked on:** project plan, stack, scope, caching strategy.
- **Decided:** stack B (FastAPI + React); SQLite + Alembic; MVP = top 5 leagues, crawl starts with EPL; the local DB is the app's only read path; post-match data only; FM24 `.rtf` as the primary FM import; hosting later, gated on a terms-of-use check. See DECISIONS.md D-001 to D-007.
- **Rejected:** Streamlit (UI ceiling), all-TypeScript (weaker stats tooling), calling the API on demand.

### 2026-11-19: Repo setup
- **Added:** README, .gitignore, .gitattributes, .editorconfig, .env.example, DECISIONS.md, docs/PLAN.md, CLAUDE.md, MEMORY.md, ERRORS.md.
- **Not started:** any code.
