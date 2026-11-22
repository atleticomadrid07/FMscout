# MEMORY.md: session log
 
**For a full briefing in a new chat, read CONTEXT.md first, then the newest session below** (it may be ahead of CONTEXT/PLAN, which are updated at decision points). Decisions and their reasons are in DECISIONS.md; the data model is in SCHEMA.md; phase status is in PLAN.md; PitchAPI facts are in PITCHAPI_NOTES.md; past mistakes are in ERRORS.md.
 
## Session 2026-11-18 → 2026-11-20 (Phases 0, 1 and most of 2)
 
- Phase 0: spike, PitchAPI field inventory, schema approved (D-001 to D-011).
- Phase 1: slices 1a–1d. EPL 2025/26 fully loaded and verified (D-012, D-013).
- Phase 2, slices 2a and 2b (D-014); template principle (D-015); CONTEXT.md written.
- **Decided, not yet in a D-entry:** hosting stays local for now (noted under D-007); Claude maintains the `claude/` doc set (top-level copies' status unconfirmed).
## Session 2026-11-20 (evening): slice 2c (done, D-016)
 
## Session 2026-11-21: slices 2d (D-017, D-018) and 2e (D-019) — PHASE 2 DONE
 
- Final: groups GK 25 · CB 67 · FB 62 · DM 28 · CM 48 · AM 17 · W 65 · ST 33; 26,693 percentiles; two offline rebuilds → identical checksums; 554 tests on the user's machine. Docs batch written (DECISIONS D-019, PLAN, SCHEMA, CONTEXT).
- User's migration IDs: heatmap depth `5019ed9e4215`, dm peer role `2f97036a9459`.
## Session 2026-11-21/22: Phase 3 (backend API)
 
- **Design drafted (2026-11-21 23:05):** `claude/PHASE3_API.md` (12 GET endpoints under `/api/v1`, slices 3a–3e, decisions 1–6).
- **User (2026-11-22 13:37): "Yeah, let's go ahead and go on 3a"** — taken as **all recommended** (stated as an assumption when 3a was delivered; record as D-020 at the 3e close-out unless the user objects): 1 in-memory rapidfuzz search; 2 public IDs = PitchAPI player IDs, scope key `l_4WFCIZ.2025-2026`; 3 sync handlers; 4 on-the-fly `min_minutes` in Phase 3; 5 custom templates not in Phase 3; 6 endpoint tests + OpenAPI snapshot. Compare as GET.
- **3a BUILT, delivered and VERIFIED by the user on EPL data** (health ok, scopes full coverage, W template, SCOPE_NOT_FOUND envelope). `api/` package: `app.py` (`create_app(database_url=None)`, lifespan opens a read-only engine and runs `check_schema_current`; a problem → data endpoints 503 `SCHEMA_OUT_OF_DATE`), `deps.py` (`read_only_url` → `sqlite:///file:/abs?mode=ro&uri=true`; `read_only_engine` uses plain `create_engine`), `errors.py`, `schemas.py`, routes meta/scopes/metrics/templates. CORS localhost:5173 only. CLI `scouting serve`. Tests `tests/unit/api/test_api_meta.py` + `api_db` fixture in `tests/conftest.py` + `openapi.json` snapshot (`UPDATE_OPENAPI=1` regenerates). Deps: fastapi 0.165.0, starlette 1.10.0, uvicorn 0.54.0, rapidfuzz 3.14.6 (user runs `uv add`; pyproject/uv.lock never shipped).
- **3b BUILT and delivered (`slice_3b.zip`, 11 files, no migration; 2026-11-22):**
  - `api/search.py`: `normalize` (accent-free, dots/hyphens → spaces), `PlayerIndex.search(q, team, role, min_minutes, limit)`; tiers EXACT 100 (name or short name), SURNAME 97, INITIAL_SURNAME 95 (initial + surname ratio ≥ 85), else WRatio capped at 94; MIN_SCORE 75; ties → more minutes, then name. Cached per scope in `app.state.search_cache` keyed on `computed_at`.
  - `routes/players.py`: `/players/search`, `/players/{id}` (card: teams, roles, peer role, 6/8 block, rating, group, `not_ranked_reason`, coverage), `/players/{id}/stats` (`metrics=` comma list keeps order; `missing` lists asked-for codes he has no line for), `/players/{id}/matches?metric=` (every appearance, oldest first, null = no data; volume metrics read the parent's rows).
  - `rate_scale`: success rates "share" (0–1); averaged rates (`value_is_rate`) "percent" or "metres" (avg_pass_length), and for those `total` is null (stored value is rate × weight) and per-match `value` is null with `rate` filled.
  - Errors: PLAYER_NOT_FOUND 404, PLAYER_NOT_IN_SCOPE 404, SCOPE_NOT_AGGREGATED 409 (only on player routes: `SCOPED_ERROR_RESPONSES`), UNKNOWN_METRIC 404, UNKNOWN_ROLE 400.
  - `deps.py`: `aggregated_scope`, `player_in_scope` → (internal id, PlayerSeason), `search_index`. `db/queries.py`: `SearchEntry`/`search_entries`, `player_id_for`, `PlayerExtras`/`player_extras`, `PlayerMatchValue`/`player_matches` (labelled columns).
  - Tests: `test_api_search.py` (12, pure), `test_api_players.py` (21), read-only checksum test extended to the player routes. 590 passed + 12 skipped in Claude's workspace (user should see 602).
- **Waiting for the user's run:** `uv add rapidfuzz` (no-op if done), unzip, pytest, serve, curls (salah, odegard, E. Haaland, rodri, g. rodriguez; Salah p_1reWwN card/stats/matches).
- **Then:** 3c `/players/{id}/template`, `/groups/{role}`; 3d `/compare` + on-the-fly `min_minutes`; 3e close-out (README API section, D-020, docs).
**Process notes:** run `ruff format` only on changed files. Tests run with `--import-mode=importlib` and no `__init__.py`: a second `conftest.py` breaks mypy (duplicate module) and test modules shouldn't import each other (mypy "found twice"); put shared fixtures in `tests/conftest.py`. Import `metrics.depth` as a module in the CLI; defaults that tests patch must be read at call time. The alembic post-write ruff hook strips unused imports from an empty revision; send paste text as `.txt`. `Result.tuples()` is deprecated in SQLAlchemy 2.1. FastAPI's OpenAPI lists a 422 on every route even though validation errors are answered as 400 BAD_REQUEST (cosmetic; revisit at 3e).
 
## How the user works (summary; full list in CONTEXT.md §7)
 
- Plan before code; options with a recommendation; wait for "go". "Walk me through / talk me through" = design discussion only.
- One zip per approved step, each explained; the user generates and runs migrations.
- Terminal steps: one block per step, no `#` comments, no `<placeholders>` (zsh); give expected results.
 