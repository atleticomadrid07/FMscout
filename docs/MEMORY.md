# MEMORY.md: session log

**For a full briefing in a new chat, read CONTEXT.md first.** This file is the session log required by `claude.md` §11. Decisions and their reasons are in DECISIONS.md; the data model is in SCHEMA.md; phase status is in PLAN.md; PitchAPI facts are in PITCHAPI_NOTES.md; past mistakes are in ERRORS.md.

## Session 2026-11-18 → 2026-11-20 (Phases 0, 1 and most of 2)

**Completed:**
- Phase 0: spike, PitchAPI field inventory, schema approved (D-001 to D-011).
- Phase 1: slices 1a–1d. EPL 2025/26 fully loaded and verified (D-012, D-013).
- Phase 2, slices 2a and 2b (D-014):
  - 2a: season totals, exposure minutes, per-90s, rates, primary roles; `scouting aggregate`, `inspect player`. Hand check passed.
  - 2b: peer groups and midpoint percentiles with minimum attempts; `report peers`, `report thresholds`. 24,893 percentiles across 7 groups; rankings plausible; checksum stable.
- Template principle agreed (D-015): process metrics, not outcomes; the user picks the metrics.
- CONTEXT.md written as the new-chat briefing.

**Decided this session, not yet in a D-entry:**
- **Hosting:** stay local for now (noted under D-007).
- **Project docs:** Claude maintains the `claude/` set; the top-level copies' status is unconfirmed by the user.

## Session 2026-11-20 (evening): slice 2c

**Built and delivered:**
- `slice_2c.zip`: `db/checks.py` (read-only checks), `scripts/check_2c.py`, display fix "85th (n=57)". The user ran it on EPL 2025/26; all tests passed on their machine.
- `slice_2c_close.zip` (407 tests pass here): `was_fouled` `season_null="zero"`; 3-4-2-1 84/86 → W; registry descriptions for `launch_pct`, `claim_rate`, `xa`, `xag`; check 5 (is `/players` xA real xA?) and `--only` in the script; tests updated (fixture peer groups are now GK 4, FB 2, W 2, ST 2; the "NULL is unknown" aggregate tests use `touches`).

**Decided (D-016):** `was_fouled` NULL = 0 (no stored 0 ever; gap vs opponent fouls flat across NULL counts, never negative). `launch_pct`/`claim_rate` = their counts' ratios × 100, rounded to 0.1; templates use `claims_won`. 3-4-2-1's 84/86 → W, **3-4-2-1 only** (user: "3421 only"); 64/66 DM and slot 95 AM kept. **xA** for templates (user supplied the docs' xAG definition; chose xA for coverage). No xA/xAG on the CB template. **6s and 8s as separate peer groups, classed by average position from heatmaps** (user picked approach C + separate percentile groups).

**Rejected:** NULL as unknown; 3-4-2-1 as AM; also changing 4-3-2-1; xAG; 6/8 by slots only, by stat profile (circular), or one group with separate templates.

**2c closing run (user's machine, 2026-11-20 23:07), all as expected:**
- `sync season --offline --renormalize`: 380/380 matches, 849,359 stat values (unchanged). **Peer groups (≥ 855 min): GK 25 · CB 67 · FB 62 · CM 76 · AM 17 · W 65 · ST 33** (before: CM 75, AM 25, W 57, ST 34). 25,034 percentiles. Roles unchanged (474 starts / 19 listed / 44 none).
- Salah `was_fouled`: 16 in 2,148 min = 0.6704 per 90 (as predicted); 13th among 65 Ws.
- **Check 5: `/players` `expected_assists` is real xA.** In rows with 0 key passes, xa > 0 in 58.8% (17.9% of season xA); xag > 0 in 0.0%. Against `/players` `chances_created` = 0: xa > 0 in 58.0%, xag > 0 in 1.4%. Label it "xA"; the docs' "not served" note refers to the advanced endpoint.

## Session 2026-11-21: slice 2d (6/8 split), spike and design

**User decisions (2026-11-21):**
- **AM at 17: accepted with the small-group warning** ("we'll have more CAMs eventually": pooling in Phase 5).
- **Depth measure: team-relative depth decides; partner-relative depth rank is the cross-check.**
- **Anchors (user's football judgement, EPL 2025/26):** 6s (DM): Moisés Caicedo, Tyler Adams, Granit Xhaka, James Garner, Carlos Baleba, Sander Berge, Manuel Ugarte. 8s (CM): Bruno Guimarães, Enzo Fernández, Alex Scott, Youri Tielemans, Anton Stach, Conor Gallagher, Alexis Mac Allister. Same-team pairs: Caicedo/Enzo (Chelsea), Adams/Scott (Bournemouth). Check each is in the central-mid peer group with enough minutes before using it (Tielemans started at 85, AM, in the spike's Villa match).

**Heatmap spike (`spikes/heatmap_spike.py`, delivered as `spike_2d_heatmaps.zip`; user ran it 2026-11-21 on m_0kLiyr Chelsea v Palace, m_1t3Zja Bournemouth v Liverpool, m_2P1f3f Villa v Newcastle; 9 requests, all HTTP 200): GO for approach C.**
- `GET /v1/matches/{id}/heatmaps` returns **one response per match with a grid for every player who played** (28/28, 31/31, 26/26) plus one per team. Shape: `{match_id, grid: {length: 16, width: 12, frame, cell_length_m: 6.5625, cell_width_m: 5.667}, teams: [{team{id,name}, side, actions, cells}], players: [{player{id,name,shirt_number}, team{id,name}, side, actions, cells: [[x, y, n], …]}]}`. Indices 0..15 × 0..11 (105 × 68 m pitch).
- **Default frame `acting_ltr` = every team attacking towards high x** (keepers 0.07–0.13, strikers 0.52–0.76). `frame=home_ltr` flips the away team (Palace keeper 0.927). Use the default.
- **Lateral axis: low y = the team's RIGHT** (Reece James 0.29, Cucurella 0.76, Salah 0.22, Gakpo 0.79): the opposite of lineup `pitch_y` (high = right). Matters for Phase 8 visuals.
- **"actions" ≈ 1.1–1.2 × `/players` touches** (median 1.17, 1.11, 1.16; range 0.50–1.52): on-ball events, including some that aren't touches.
- **Cost: 1 request per match** → +380 per EPL season (~6.5 min at 1 req/s).
- Pass network (`/advanced/network`): per team, `nodes` with `avg_x`/`avg_y` (metres), `passes`, `degree`, `betweenness`…, only **until the first substitution** (`window.kind = first_substitution`), starters only. `avg_x` correlates r = 0.94–0.97 with heatmap depth. Not needed (heatmaps cover all minutes and players), but a valid fallback.
- **Anchor pairs, single match:** Caicedo x 0.470 (rel −0.059) vs Enzo 0.545 (+0.016) ✓; Adams 0.391 (−0.082) vs Scott 0.525 (+0.051) ✓. Other mids (rel to outfield mean): Hughes −0.053 / Wharton +0.035 (Palace); Mac Allister −0.002 / Szoboszlai +0.006 (Liverpool, near-equal); Onana −0.024 / Kamara −0.012 (Villa, two 6s); Tonali (single pivot) −0.004, Joelinton +0.008, Bruno +0.058 (Newcastle). Partner rank alone would have called Kamara and Szoboszlai 8s: confirms it's only a cross-check. Gaps are small (≈ 0.01–0.13 of pitch length) in single matches; season averaging is essential.

**Next:** Claude presented the 2d design (2026-11-21) for approval: three sub-slices (2d-1 ingest heatmaps and store per-appearance depth; 2d-2 read-only calibration report; 2d-3 classification, DM peer group, migration). Waiting for the user's answers. All docs (PLAN, DECISIONS D-016 addendum + D-017, SCHEMA, PITCHAPI_NOTES, CONTEXT) to be updated once the design is approved, together with 2c's closing numbers.

**Process note:** running `ruff format` on whole folders reformatted `settings.py` (an unrelated file); restored from the original. Format only the files being changed.

## How the user works (summary; full list in CONTEXT.md §7)

- Plan before code; options with a recommendation; wait for "go". "Don't start coding first, walk me through the thought process" = design discussion only.
- Zips of changed files for review; the user generates and runs migrations.
- Terminal steps: one block per step, no `#` comments, no `<placeholders>` (zsh).