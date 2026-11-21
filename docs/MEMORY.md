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

## Session 2026-11-20 (evening): slice 2c (done)

- Built `db/checks.py` + `scripts/check_2c.py` + the "85th (n=57)" display fix; the user ran the checks on EPL 2025/26.
- **Decided (D-016):** `was_fouled` NULL = 0; `launch_pct`/`claim_rate` are their counts' ratios (templates use `claims_won`); 3-4-2-1's 84/86 → W (3-4-2-1 only); xA over xAG (verified real xA by check 5); no xA on the CB template; 6s and 8s to become separate peer groups by heatmap depth.
- **Closing run:** peer groups GK 25 · CB 67 · FB 62 · CM 76 · AM 17 · W 65 · ST 33; 25,034 percentiles; Salah `was_fouled` 0.6704 per 90.

## Session 2026-11-21: slice 2d (6/8 split)

**User decisions (D-017):** AM at 17 accepted with the warning (pooling later). Team-relative depth decides; partner rank is a cross-check. ≥ 5 central-mid starts with heatmaps to classify by depth, else formation fallback. Plan approved as steps, **one zip per step, each explained; "go" before each step**. Anchors (provisional, "don't take my words as 100%"): 6s Caicedo, T. Adams, Xhaka, Garner, Baleba, Berge, Ugarte; 8s Bruno G., Enzo F., A. Scott, Tielemans, Stach, Gallagher, Mac Allister.

**Heatmap spike** (`spike_2d_heatmaps.zip`): per-player heatmaps, one request per match, `acting_ltr` = every team attacks towards high x. Details in PITCHAPI_NOTES.

**2d-1 DONE** (`slice_2d1.zip`): migration (3 `ADD COLUMN`s) applied; online `sync season` made exactly 380 requests, all heatmaps `ok`; spot checks matched the spike (Acheampong 0.379 / 139 actions; Onana 0.443 / 65).

**2d-2 DONE** (`slice_2d2.zip`: `metrics/depth.py` + `scouting report depth`, 469 tests). **User's run on EPL 2025/26 (2026-11-21 13:46):**
- **No natural gap:** one smooth bell under both references; ~a dozen players within 1 m of any cut.
- **outfield reference:** cut −0.014 (−1.5 m), margin 0.002; anchor gap Xhaka −1.7 m vs Mac Allister −1.3 m (0.4 m); DM 33 · CM 42 · fallback 1 (Bergvall, 4 starts); Garner (+1.5 m) on the wrong side; Rice DM (−2.5 m).
- **centre_backs reference:** cut +0.133 (+13.9 m), margin 0.012; anchor gap Berge +12.6 m vs Scott +15.2 m (2.6 m); DM 29 · CM 46 · fallback 1; Garner (+17.8 m) on the wrong side; **Rice CM (+14.3 m), correct as Arsenal's left 8.**
- Cross-check disagreement under both: **Bernardo Silva** (DM by depth: +12.8 m from CBs; deepest of City's mids only 13% of 23 starts; Rodri/Nico González deeper).
- Notable labels (outfield `--all`): deepest Adams, Bogarde, Zubimendi, Rodri, Nico González…; highest Hannibal, Munetsi, Stach, Foden, Reijnders…

**User decisions after the report (2026-11-21 13:49):**
1. Asked whether the two references can be combined → Claude's answer: a simple average of the two (from the report's rounded values) narrows the anchor gap to ≈ 1.7 m (Xhaka ≈ 5.4 m vs Mac Allister ≈ 7.1 m) and flips Rice back to DM, so it dilutes the better signal; recommended the **centre-back line alone, with the outfield value kept visible as a second opinion**. Waiting for the user to confirm.
2. **Cut: 14 m in front of the centre-backs** (0.1333 of the pitch length) — recommended, accepted.
3. **Garner moves to the 8 anchors** — accepted.
4. **Depth decides, but Bernardo should be an 8**; the user asked for a way to detect "a significantly deeper midfielder". Claude proposed a **partner gap** rule: per match, his depth minus the depth of his deepest fellow central mid; season minutes-weighted; a player labelled DM by depth whose deepest partner is on average ≥ G metres deeper becomes CM ("an 8 beside a deeper 6"). Uses the size of the gap, not the rank, so close double 6s (Onana/Kamara) stay DM. G to be chosen from data: proposed step **2d-2b** = extend `report depth` (centre-back reference + 14 m cut + Garner as 8 as defaults; partner gap column; who flips at candidate G of 2/3/4/5 m). Waiting for "go".
5. **Store season depth and the decision method; show depth with the label (e.g. "DM · 12.6 m in front of his CBs", borderline within ~1 m).** User: **in the UI (Phase 4) this display must be removable/toggleable in case it looks out of shape.**

**Record as D-018 once G is chosen** (then update PLAN, CONTEXT, DECISIONS, SCHEMA).

**Process notes:** run `ruff format` only on changed files (it once reformatted `settings.py`). Test throwaway scripts on fake shapes too. The fixture reuses player names across teams: rename players in tests that look anchors up by name.

## How the user works (summary; full list in CONTEXT.md §7)

- Plan before code; options with a recommendation; wait for "go". "Walk me through the thought process" = design discussion only.
- One zip per approved step, each explained; the user generates and runs migrations.
- Terminal steps: one block per step, no `#` comments, no `<placeholders>` (zsh).