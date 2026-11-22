# MEMORY.md: session log

**For a full briefing in a new chat, read CONTEXT.md first, then the newest session below** (it may be ahead of CONTEXT/PLAN, which are updated at decision points). Decisions and their reasons are in DECISIONS.md; the data model is in SCHEMA.md; phase status is in PLAN.md; PitchAPI facts are in PITCHAPI_NOTES.md; past mistakes are in ERRORS.md.

## Session 2026-11-18 → 2026-11-20 (Phases 0, 1 and most of 2)

**Completed:**
- Phase 0: spike, PitchAPI field inventory, schema approved (D-001 to D-011).
- Phase 1: slices 1a–1d. EPL 2025/26 fully loaded and verified (D-012, D-013).
- Phase 2, slices 2a and 2b (D-014): season aggregates, per-90s, roles; peer groups and midpoint percentiles. Hand checks passed; checksum stable.
- Template principle agreed (D-015): process metrics, not outcomes; the user picks the metrics.
- CONTEXT.md written as the new-chat briefing.

**Decided, not yet in a D-entry:**
- **Hosting:** stay local for now (noted under D-007).
- **Project docs:** Claude maintains the `claude/` set; the top-level copies' status is unconfirmed by the user.

## Session 2026-11-20 (evening): slice 2c (done, D-016)

- `db/checks.py` + `scripts/check_2c.py` + "85th (n=57)" display; `was_fouled` NULL = 0; 3-4-2-1's 84/86 → W; xA over xAG.
- **Closing run:** peer groups GK 25 · CB 67 · FB 62 · CM 76 · AM 17 · W 65 · ST 33; 25,034 percentiles.

## Session 2026-11-21: slice 2d (6/8 split; D-017, D-018) — DONE

- Final groups **GK 25 · CB 67 · FB 62 · DM 28 · CM 48 · AM 17 · W 65 · ST 33**; split by depth 73 · deeper partner 2 · formation 1. Docs updated.
- User's migration IDs: heatmap depth `5019ed9e4215`, dm peer role `2f97036a9459`. Send migration paste text as `.txt` (ERRORS.md).

## Session 2026-11-21 (evening): slice 2e (templates) — 2e-3 delivered, then close-out

**User decisions (to record as D-019 in the close-out doc batch):**
- **Phase 2 ends with 2e + a close-out step:** full offline rebuild run twice with equal checksums, README update. (The "6 or 8" line move was done early, in 2e-3.)
- **Custom templates:** three foundations built in 2e-3. Storage (separate `user.db`), API and edit UI in Phases 3–4; whether "duplicate and edit" is MVP decided then.
- **Backlog:** cross-position templates and weights/composite scores (label which group each percentile is from or rank against one chosen group; weights double-count correlated metrics, percentiles are ranks, ineligible metrics, false precision → section weights, breakdown bars, rank order). Possession-adjusted volume stats.
- **A–E approved (17:24):** A volume metrics next to success rates (built as derived metrics, no renormalize); B dribbles_completed over take_ons_completed, chances_created over key_passes (/players, coverage + FM names); C VAEP/PV on the player card, not templates; D template size 8–14 (GK 8); E passes_completed min_attempts 200 → 100.
- **Template file format: TOML** (stdlib `tomllib`, no new dependency; user 17:39). The FM mapping file (Phase 6) should use TOML too.
- **Picks:** exactly as in `src/scouting/mappings/position_templates.toml` (GK 8; others 12). W deliberately attacking (no out-of-possession). ST keeps xgot (includes penalties, accepted) and defensive_actions (accepted).

**Run results:**
- **2e-2 (17:38):** 531 passed; aggregate groups unchanged; **percentiles 26,693** (25,034 + 1,650 volume + 9 more on pass completion, exactly as predicted).
- **check_2e:** xGOT includes penalties (20/20 single-shot scored penalties have xGOT, mean 0.794; control 271/271). defensive_actions = tackles + interceptions + clearances + shot_blocks + recoveries in 68.3% of 10,725 outfield appearances, otherwise ~1.09 lower on average (overlaps probably counted once; unverified). Descriptions updated in the registry.

**2e-3 BUILT and delivered (`slice_2e3.zip`, ~17:45, 6 files, no migration):**
- `mappings/position_templates.toml` (8 templates with sections; comments record the user's choices).
- `metrics/templates.py`: `Template`/`TemplateSection` (id `builtin:ROLE`, `builtin` flag), `parse_templates()` (pydantic shape check, `TemplateError`), `builtin_templates()` (importlib.resources), `Finding(severity, rule, message, code)`, `check_template()` (errors: unknown_metric, wrong_position via applies_to, duplicate, empty, unknown_role; built-in errors / custom warnings: outcome, near_duplicate (xa/xag, key_passes/chances_created, dribbles/take_ons, claims_won/claim_rate), size 8–14), `check_coverage()` (warning when < 50% of the group is ranked), `apply_template(PlayerSeason, Template) -> TemplateView` (rows with line, pct, ranked_among, basis, model flag, direction, reason; `same_group` flag when the template is for another position), `missing_reason()`.
- CLI: `inspect player … --template` (sections with values and percentiles or the reason), `report templates --league --season`; "6 or 8" line now after "as a starter".
- Registry: xgot and defensive_actions descriptions from check_2e.
- Tests: `tests/unit/metrics/test_templates.py` (18) + 4 CLI tests. 541 passed + 12 skipped (user: 553).
- **Expected on real data:** `report templates` → "all templates pass" (lowest coverage: FB crosses 57/62, GK claims 23/25, W dribbles 63/65, ST passes at 100 ≈ 32/33).
- **Waiting for the user's run.** Then the close-out zip: README update, offline rebuild twice + checksum; then the doc batch: D-019 in DECISIONS, SCHEMA (registry `outcome`/`volume_of`, volume metrics, templates), PLAN (2e done, Phase 2 done), CONTEXT, TEMPLATE_OPTIONS (mark as decided).

**Process notes:** run `ruff format` only on changed files. Test throwaway scripts on fake shapes too. The fixture reuses player names across teams: rename players in tests that look anchors up by name. Import `metrics.depth` as a module in the CLI; defaults that tests patch must be read at call time. The alembic post-write ruff hook strips unused `sa`/`op` imports from an empty revision. The fixture's lineups have no second DM, so relabelled left-backs are lone pivots. Ruff flags `×` in strings/comments (RUF001/RUF003): use `x`; and SIM300 flags `const <= set(...)`-style asserts. `Result.tuples()` is deprecated in SQLAlchemy 2.1: unpack rows instead. At the 100-pass minimum one fixture full-back qualifies. The fixture season has no CB/DM/CM/AM groups.

## How the user works (summary; full list in CONTEXT.md §7)

- Plan before code; options with a recommendation; wait for "go". "Walk me through the thought process" = design discussion only.
- One zip per approved step, each explained; the user generates and runs migrations.
- Terminal steps: one block per step, no `#` comments, no `<placeholders>` (zsh).