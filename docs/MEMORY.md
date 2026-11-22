# MEMORY.md: session log

**For a full briefing in a new chat, read CONTEXT.md first, then the newest session below** (it may be ahead of CONTEXT/PLAN, which are updated at decision points). Decisions and their reasons are in DECISIONS.md; the data model is in SCHEMA.md; phase status is in PLAN.md; PitchAPI facts are in PITCHAPI_NOTES.md; past mistakes are in ERRORS.md.

## Session 2026-11-18 → 2026-11-20 (Phases 0, 1 and most of 2)

- Phase 0: spike, PitchAPI field inventory, schema approved (D-001 to D-011).
- Phase 1: slices 1a–1d. EPL 2025/26 fully loaded and verified (D-012, D-013).
- Phase 2, slices 2a and 2b (D-014); template principle (D-015); CONTEXT.md written.
- **Decided, not yet in a D-entry:** hosting stays local for now (noted under D-007); Claude maintains the `claude/` doc set (top-level copies' status unconfirmed).

## Session 2026-11-20 (evening): slice 2c (done, D-016)

## Session 2026-11-21: slices 2d (D-017, D-018) and 2e (D-019) — PHASE 2 DONE

- **2d:** heatmap depth, `report depth`, 6/8 classification (CB reference, 14 m cut, deeper partner ≥ 2 m, single-pivot fallback), DM peer group. User's migration IDs: heatmap depth `5019ed9e4215`, dm peer role `2f97036a9459`.
- **2e:** `template_options.py` + `claude/TEMPLATE_OPTIONS.md`; decisions A–E; 5 derived volume metrics; outcome flag; pass minimum 100; `check_2e.py` (xGOT includes penalties; defensive_actions ≈ 5 parts in 68%); `position_templates.toml` (TOML chosen) + `metrics/templates.py` foundations; `inspect player --template`, `report templates` (all pass).
- **Close-out (17:56):** README rewritten (repo root), exact-name player lookup ("rodri" → Rodri), "6 or 8" after the starts breakdown; **two offline rebuilds → identical checksums**. Final: groups GK 25 · CB 67 · FB 62 · DM 28 · CM 48 · AM 17 · W 65 · ST 33; 26,693 percentiles; 554 tests on the user's machine.
- **Docs batch written (after 17:56):** DECISIONS (D-019), PLAN (Phase 2 done), SCHEMA, CONTEXT. TEMPLATE_OPTIONS left as the history of the picks (the TOML is authoritative; noted in D-019 and CONTEXT).
- **Next: Phase 3 (backend API), plan first.** Open questions for the plan are listed in CONTEXT §3 and PLAN.

**Process notes:** run `ruff format` only on changed files. Import `metrics.depth` as a module in the CLI; defaults that tests patch must be read at call time. The alembic post-write ruff hook strips unused `sa`/`op` imports from an empty revision; send paste text as `.txt`. Ruff flags `×` (RUF001/RUF003) and SIM300 (`const <= set(...)`). `Result.tuples()` is deprecated in SQLAlchemy 2.1. The fixture season has no CB/DM/CM/AM groups; its lineups have no second DM; at the 100-pass minimum one fixture full-back qualifies. The short-name lookup isn't accent-insensitive (Phase 3 search fixes it).

## How the user works (summary; full list in CONTEXT.md §7)

- Plan before code; options with a recommendation; wait for "go". "Walk me through / talk me through" = design discussion only.
- One zip per approved step, each explained; the user generates and runs migrations.
- Terminal steps: one block per step, no `#` comments, no `<placeholders>` (zsh); give expected results.