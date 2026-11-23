# MEMORY.md: session log

**For a full briefing in a new chat, read CONTEXT.md first, then the newest session below** (it may be ahead of CONTEXT/PLAN, which are updated at decision points). Decisions and their reasons are in DECISIONS.md; the data model is in SCHEMA.md; phase status is in PLAN.md; the API design is in PHASE3_API.md; PitchAPI facts are in PITCHAPI_NOTES.md; past mistakes are in ERRORS.md.

## Session 2026-11-18 → 2026-11-20 (Phases 0, 1 and most of 2)

- Phase 0: spike, PitchAPI field inventory, schema approved (D-001 to D-011).
- Phase 1: slices 1a–1d. EPL 2025/26 fully loaded and verified (D-012, D-013).
- Phase 2, slices 2a and 2b (D-014); template principle (D-015); CONTEXT.md written.
- **Decided, not yet in a D-entry:** hosting stays local for now (noted under D-007); Claude maintains the `claude/` doc set (top-level copies' status unconfirmed).

## Session 2026-11-20 (evening): slice 2c (done, D-016)

## Session 2026-11-21: slices 2d (D-017, D-018) and 2e (D-019) — PHASE 2 DONE

- Final: groups GK 25 · CB 67 · FB 62 · DM 28 · CM 48 · AM 17 · W 65 · ST 33; 26,693 percentiles; two offline rebuilds → identical checksums; 554 tests on the user's machine. Docs batch written (DECISIONS D-019, PLAN, SCHEMA, CONTEXT).
- User's migration IDs: heatmap depth `5019ed9e4215`, dm peer role `2f97036a9459`.

## Session 2026-11-21/22: Phase 3 (backend API), slices 3a–3c

- **Design** `claude/PHASE3_API.md` (2026-11-21); the user's "go on 3a" (2026-11-22) taken as all six recommendations.
- **3a built and verified** (skeleton, read-only engine, error envelope, scopes, metrics, templates, `scouting serve`, OpenAPI snapshot).
- **3b built (`slice_3b.zip`) and verified on EPL data (2026-11-22 14:07):** search, card, stats, per-match values; details in D-020 and PHASE3_API §8.
- **User (14:09): "we can sort out search scoring later -> let's update the various docs right now".** Search fuzzy-noise tuning → PLAN backlog. **Docs batch written** (D-020, PLAN, CONTEXT, PHASE3_API, ERRORS +3, MEMORY).
- **User (20:52): "Okay go on 3c"** — approves the PHASE3_API §9 proposal.
- **3c built (`slice_3c.zip`, 10 files, no migration):**
  - `GET /players/{id}/template?scope=&template=`: `template` = id (`builtin:W`) or role shortcut (`W`); default = his peer role's; rows are `StatLineOut` (identical to `/stats` lines; a metric with no data → minutes 0, nulls, reason "no data"); `template.same_group` false for another position (still ranked in his own group). Errors `UNKNOWN_TEMPLATE` 404, `TEMPLATE_REQUIRED` 400 (no peer role and no `template=`).
  - `GET /groups/{role}?scope=&metrics=&sort=`: members = stored peer group (role, ≥ group min minutes); `metrics` default = the role's template codes, max 14 (`MAX_METRICS`, the largest template size; the proposal said 12); `values` is a dict per player keyed by metric code: value (per 90 or rate), total (null for averaged rates), attempts, minutes, pct, reason; `metrics` header with basis, rate_scale, model, direction, ranked_among; sorted best first on `sort` (default first metric; lower-is-better ascending; no value last; then name). Errors `UNKNOWN_ROLE` 400, `GROUP_NOT_FOUND` 404 (new code), `UNKNOWN_METRIC` 404, `BAD_REQUEST` (sort not among metrics, > 14 metrics, empty list).
  - Refactors: shared mapping helpers moved from `routes/players.py` to new `api/mapping.py` (`group_out`, `provenance`, `basis`, `rate_scale`, `stat_line_out`, `empty_line_out`, `metric_codes` (dedupes), `check_role`); `metrics/templates.missing_reason` split → `stat_reason(...)` (per-line eligibility reason, reused by `/groups`); `db/queries.group_view` → `GroupView`/`GroupMember`/`GroupStat`.
  - Tests: `tests/unit/api/test_api_groups.py` (14), read-only checksum test covers `/template` and `/groups`; snapshot regenerated. 604 passed + 12 skipped in Claude's workspace.
- **3c VERIFIED by the user (2026-11-22 21:05):** "all tests passed" (616 expected). Salah's W radar via the API = `inspect player --template` on all 12 metrics (npxG 88.5, shots 93.1, box touches 94.6, xA 86.9, chances created 86.9, passes into box 96.2, crosses/90 76.2, cross completion 24.2 of 60, dribbles/90 26.9, dribble success 0.79 of 63, progressive carries 68.5, carries into box 79.2). `/groups/W?metrics=npxg,xa`: size 65, top 10 = `report peers --role W --metric npxg` in order and value (Brooks 0.4376 99.2 … Okafor 0.3019 85.4). `template=ST` for Salah → `same_group: False`, group W.
- **Next: 3d** (`/compare` + on-the-fly `min_minutes`): detailed proposal sent 2026-11-22 21:10 with decisions A–D, waiting for the user's answers / "go". Then 3e close-out (README API section, D-020 completed, docs).

**Process notes:** run `ruff format` only on changed files. Tests run with `--import-mode=importlib` and no `__init__.py`: one `conftest.py`, test modules never import each other (ERRORS.md). Import `metrics.depth` as a module in the CLI; defaults that tests patch must be read at call time. The alembic post-write ruff hook strips unused imports from an empty revision; send paste text as `.txt`. `Result.tuples()` is deprecated in SQLAlchemy 2.1. Workspace: `UV_NATIVE_TLS` is deprecated, use `UV_SYSTEM_CERTS=1`. `metrics.percentiles.rank_against` reads the *stored* percentile population (default threshold only); 3d needs a population source that works for a custom `min_minutes`.

## How the user works (summary; full list in CONTEXT.md §7)

- Plan before code; options with a recommendation; wait for "go". "Walk me through / talk me through" = design discussion only.
- One zip per approved step, each explained; the user generates and runs migrations.
- Terminal steps: one block per step, no `#` comments, no `<placeholders>` (zsh); give expected results.
- Defers things explicitly ("later") — put them in the backlog and don't push them.