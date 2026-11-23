# MEMORY.md: session log

**For a full briefing in a new chat, read CONTEXT.md first, then the newest session below** (it may be ahead of CONTEXT/PLAN, which are updated at decision points). Decisions and their reasons are in DECISIONS.md; the data model is in SCHEMA.md; phase status is in PLAN.md; the API as built is in PHASE3_API.md; PitchAPI facts are in PITCHAPI_NOTES.md; past mistakes are in ERRORS.md.

## Session 2026-11-18 → 2026-11-20 (Phases 0, 1 and most of 2)

- Phase 0: spike, PitchAPI field inventory, schema approved (D-001 to D-011).
- Phase 1: slices 1a–1d. EPL 2025/26 fully loaded and verified (D-012, D-013).
- Phase 2, slices 2a and 2b (D-014); template principle (D-015); CONTEXT.md written.
- **Decided, not yet in a D-entry:** hosting stays local for now (noted under D-007); Claude maintains the `claude/` doc set (top-level copies' status unconfirmed).

## Session 2026-11-20 (evening): slice 2c (done, D-016)

## Session 2026-11-21: slices 2d (D-017, D-018) and 2e (D-019) — PHASE 2 DONE

- Final: groups GK 25 · CB 67 · FB 62 · DM 28 · CM 48 · AM 17 · W 65 · ST 33; 26,693 percentiles; two offline rebuilds → identical checksums; 554 tests on the user's machine.
- User's migration IDs: heatmap depth `5019ed9e4215`, dm peer role `2f97036a9459`.

## Session 2026-11-21/22: Phase 3 (backend API) — PHASE 3 DONE (D-020)

- **Design** `claude/PHASE3_API.md`; the user's "go on 3a" taken as all six recommendations.
- **3a, 3b, 3c, 3d built and each verified by the user on EPL 2025/26** (details and numbers in D-020 and CONTEXT §2). 3c/3d: "all tests passed".
- **User (14:09): search scoring "later"** → PLAN backlog.
- **3d decisions (21:14):** A first player's template; B outsiders vs members only (FM "bananas stats"); C `min_minutes` on all four endpoints; D minimum 90.
- **3e (21:30, user: "fix A, … short section plus a pointer to /docs for B → before starting on phase 4, let's discuss first"):** built and delivered `slice_3e.zip` (5 files: repo-root `README.md` with a "Using the API" section + status/stack/layout/roadmap updates; `api/errors.py` `install_openapi` drops FastAPI's 422s and the `HTTPValidationError`/`ValidationError` schemas; `api/app.py` calls it; `tests/unit/api/test_api_meta.py` `test_openapi_lists_400_not_422`; regenerated `openapi.json`, which differs from 3d only by the removed 422s/schemas). 621 passed + 12 skipped in Claude's workspace → **633 expected** on the user's machine (not yet confirmed).
- **Docs batch written for the Phase 3 close-out:** DECISIONS (D-020 completed: 3c, 3d with the user's reasons, Claude's extra calls, 3e, rejected options), PLAN (Phase 3 done, Phase 3 slice table, Phase 4 next with discussion topics), PHASE3_API (as built, final), CONTEXT (rewritten for "Phase 3 done, discuss Phase 4"), MEMORY. SCHEMA, PITCHAPI_NOTES, ERRORS unchanged.
- **Next: Phase 4 discussion** (no design or code until the user says so). Topics listed in CONTEXT §3 and PLAN "Still open".

**Process notes:** run `ruff format`/`ruff check --fix` only on changed files (whole-folder `--fix` also "fixes" the user's `settings.py` W292; keep it as they have it, never ship it). Tests run with `--import-mode=importlib` and no `__init__.py`: one `conftest.py`, test modules never import each other. Import `metrics.depth` as a module in the CLI; defaults that tests patch must be read at call time. Send migration paste text as `.txt`. `Result.tuples()` is deprecated in SQLAlchemy 2.1. Workspace: use `UV_SYSTEM_CERTS=1` (not `UV_NATIVE_TLS`).

## How the user works (summary; full list in CONTEXT.md §7)

- Plan before code; options with a recommendation; wait for "go". "Walk me through / talk me through / describe / let's discuss first" = discussion only.
- One zip per approved step, each explained; the user generates and runs migrations.
- Terminal steps: one block per step, no `#` comments, no `<placeholders>` (zsh); give expected results.
- Defers things explicitly ("later") — put them in the backlog and don't push them.
- Answers to a decision list count as the go-ahead for that slice; asks for expansion when an option is unclear.