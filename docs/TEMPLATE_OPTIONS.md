# TEMPLATE_OPTIONS.md: metric options per position (slice 2e)

From `scripts/template_options.py`, EPL 2025/26, peer groups at ≥ 855 min. Pick per position; reply with codes like "ST: drop 11, add 4". ★ = Claude's suggested set (about 12; GK fewer). "ranked" is shown only where it's below the full group.

**Values:** median (90th-percentile value in brackets), per 90 unless shown as %. **(model)** = a provider model output (xG, xA, xT, VAEP…), flagged as such in the UI. **↓** = lower is better (the UI flips it). **new** = needs a volume metric that doesn't exist yet (decision A below).

**Left off every list (D-015 and the data):**
- **Outcomes:** goals, assists, GCA, second assists, big chances missed, goals conceded. Shown elsewhere on the page.
- **Rare events, mostly tied at zero** (zero share 50–100% in most groups): woodwork, clearances off the line, last-man tackles (except CB, as an option), errors leading to a goal, penalties won/conceded/missed/saved, own goals, corners (and corners measure who takes set pieces, not quality).
- **Team, not player:** xGOT faced (the keeper's defence), touches and distributions as pure volume.
- **VAEP / PV defensive splits:** medians are near zero or negative for most outfield groups (CB VAEP defensive −0.029), so the percentiles rank noise.
- **Overall value models (VAEP, PV):** suggested for the player card next to the rating, not on templates, because they overlap with every component already on the radar (decision C).

---

## GK (25)

| # | Section | Metric | Value | Note |
|---|---|---|---|---|
| 1 ★ | Shot-stopping | goals_prevented (model) | 0.00 (0.15) | The only quality measure of shot-stopping; save % isn't in the registry |
| 2 | Shot-stopping | saves | 2.87 (3.44) | Workload: depends on how many shots the defence allows |
| 3 | Shot-stopping | saves_inside_box | 1.91 (2.30) | Same caveat |
| 4 | Shot-stopping | diving_saves | 1.24 (1.91) | Same caveat; partly style |
| 5 ★ | Box | claims_won (rate) | 89.6% (95.9%), ranked 23 | Exact ratio (D-016) |
| 6 ★ | Box | high_claims | 0.69 (1.23) | How often he comes for crosses |
| 7 ★ | Box | punches | 0.67 (1.12) | Style |
| 8 ★ | Sweeping | sweeper_actions | 0.60 (0.93) | |
| 9 ★ | Distribution | distribution_accuracy | 68.9% (78.7%) | |
| 10 ★ | Distribution | launch_pct | 44.2% (55.1%) | Style, no direction |
| 11 | Distribution | avg_pass_length | 35.4 (40.0) m | Style; overlaps 10 |
| 12 ★ | Distribution | progressive_passes | 0.09 (0.21) | 8% zero; thin spread |
| 13 | Distribution | passes_completed (rate) | 63.7% (76.8%) | Overlaps 9 |
| 14 | Distribution | xt (model) | 0.030 (0.042) | Tiny spread |

## CB (67)

| # | Section | Metric | Value | Note |
|---|---|---|---|---|
| 1 ★ | Defending | tackles | 1.39 (2.32) | |
| 2 ★ | Defending | interceptions | 0.95 (1.52) | |
| 3 ★ | Defending | clearances | 5.76 (7.19) | Higher for deep-defending teams |
| 4 | Defending | headed_clearances | 3.27 (4.52) | Overlaps 3 |
| 5 ★ | Defending | shot_blocks | 0.75 (1.10) | |
| 6 | Defending | recoveries | 3.40 (4.30) | |
| 7 ★ | Defending | dribbled_past ↓ | 0.36 (0.69) | Also rises with how much he defends |
| 8 | Defending | last_man_tackles | 0.06 (0.20) | 22% zero |
| 9 | Defending | defensive_actions | 9.06 (10.95) | Composition unverified; overlaps 1–5 |
| 10 ★ | Duels | ground_duels_won (rate) | 60.3% (67.6%), ranked 65 | |
| 11 ★ | Duels | aerials_won (rate) | 58.1% (68.8%) | |
| 12 ★ | Duels | aerials won per 90 | new | Volume next to the success rate |
| 13 ★ | Passing | passes_completed (rate) | 87.3% (92.0%) | |
| 14 ★ | Passing | long_balls_completed (rate) | 42.7% (50.3%) | |
| 15 ★ | Passing | progressive_passes | 1.99 (3.73) | |
| 16 | Passing | progressive_pass_distance | 40.8 (77.2) m | Overlaps 15 |
| 17 | Passing | passes_final_third | 5.53 (9.32) | |
| 18 ★ | Carrying | progressive_carries | 0.65 (1.21) | |
| 19 | Progression | xt (model) | 0.064 (0.100) | |
| 20 | Passing | switches | 0.36 (0.78) | |
| 21 | Progression | xg_buildup (model) | 0.61 (0.93) | |
| 22 | Security | miscontrols ↓ / dispossessed ↓ | 0.47 / 0.20 | |
| 23 | Set pieces | npxg (model) | 0.053 (0.100) | Set-piece threat |
| 24 | Set pieces | touches_opp_box | 1.27 (2.20) | |

## FB (62)

| # | Section | Metric | Value | Note |
|---|---|---|---|---|
| 1 ★ | Defending | tackles | 1.97 (2.79) | |
| 2 ★ | Defending | interceptions | 1.01 (1.44) | |
| 3 ★ | Defending | recoveries | 4.14 (5.16) | |
| 4 ★ | Defending | dribbled_past ↓ | 0.72 (1.31) | |
| 5 ★ | Defending | ground_duels_won (rate) | 54.2% (62.4%), ranked 61 | |
| 6 | Defending | aerials_won (rate) | 50.0% (62.4%), ranked 60 | |
| 7 | Defending | clearances | 2.80 (4.00) | |
| 8 ★ | Progression | progressive_carries | 1.40 (2.52) | |
| 9 ★ | Progression | progressive_passes | 2.49 (3.66) | |
| 10 | Progression | carries_final_third | 1.18 (1.84) | |
| 11 | Progression | passes_final_third | 5.09 (7.39) | |
| 12 ★ | Progression | xt (model) | 0.095 (0.146) | |
| 13 ★ | Creation | chances_created | 0.86 (1.44) | See decision B |
| 14 ★ | Creation | xa (model) | 0.082 (0.137) | |
| 15 ★ | Creation | crosses completed per 90 | new | |
| 16 ★ | Creation | crosses_completed (rate) | 22.2% (31.5%), ranked 57 | |
| 17 | Creation | passes_into_box | 0.91 (1.69) | |
| 18 | Creation | sca | 2.08 (3.51) | |
| 19 | Creation | touches_opp_box | 1.56 (3.30) | |
| 20 | Security | dispossessed ↓ / miscontrols ↓ | 0.60 / 1.22 | |
| 21 | Security | passes_completed (rate) | 83.4% (87.2%) | |

## DM (28)

| # | Section | Metric | Value | Note |
|---|---|---|---|---|
| 1 ★ | Ball-winning | tackles | 2.43 (3.73) | Highest median of any group |
| 2 ★ | Ball-winning | interceptions | 1.25 (1.88) | Highest of any group |
| 3 ★ | Ball-winning | recoveries | 4.95 (6.33) | Highest of any group |
| 4 ★ | Ball-winning | ground_duels_won (rate) | 55.7% (62.6%) | |
| 5 ★ | Ball-winning | aerials_won (rate) | 49.2% (64.7%) | |
| 6 ★ | Ball-winning | dribbled_past ↓ | 0.90 (1.43) | |
| 7 | Ball-winning | shot_blocks | 0.46 (0.68) | |
| 8 | Ball-winning | clearances | 2.13 (2.65) | |
| 9 | Ball-winning | fouls_committed ↓ | 1.20 (1.75) | |
| 10 ★ | Distribution | passes_completed (rate) | 86.5% (90.2%) | |
| 11 ★ | Distribution | long_balls_completed (rate) | 52.8% (66.3%), ranked 27 | |
| 12 ★ | Distribution | progressive_passes | 2.30 (3.84) | |
| 13 ★ | Distribution | passes_final_third | 5.57 (9.19) | |
| 14 | Distribution | switches | 0.44 (0.76) | |
| 15 ★ | Distribution | xg_buildup (model) | 0.77 (1.02) | Involvement in moves before the final two actions |
| 16 | Distribution | xt (model) | 0.043 (0.095) | |
| 17 | Carrying | progressive_carries | 0.95 (1.54) | |
| 18 ★ | Security | dispossessed ↓ | 0.61 (1.03) | |
| 19 | Security | miscontrols ↓ | 1.02 (1.37) | |

## CM (48)

| # | Section | Metric | Value | Note |
|---|---|---|---|---|
| 1 ★ | Progression | progressive_passes | 2.43 (4.73) | |
| 2 ★ | Progression | progressive_carries | 1.31 (2.40) | |
| 3 ★ | Progression | passes_final_third | 5.95 (9.03) | |
| 4 | Progression | carries_final_third | 1.21 (1.95) | |
| 5 ★ | Progression | xt (model) | 0.073 (0.142) | |
| 6 | Progression | switches | 0.53 (1.30) | |
| 7 ★ | Creation | chances_created | 1.19 (2.02) | |
| 8 ★ | Creation | xa (model) | 0.101 (0.208) | |
| 9 ★ | Creation | passes_into_box | 1.27 (2.64) | |
| 10 | Creation | sca | 2.66 (4.58) | |
| 11 | Creation | through_balls | 0.22 (0.61) | 12.5% zero |
| 12 ★ | Box arrivals | npxg (model) | 0.081 (0.167) | |
| 13 ★ | Box arrivals | touches_opp_box | 1.71 (2.96) | |
| 14 | Box arrivals | shots | 1.12 (1.85) | |
| 15 ★ | Defending | tackles | 1.96 (3.00) | |
| 16 ★ | Defending | recoveries | 4.71 (5.70) | |
| 17 | Defending | interceptions | 1.00 (1.37) | |
| 18 | Defending | ground_duels_won (rate) | 51.8% (57.2%) | |
| 19 ★ | Security | passes_completed (rate) | 85.1% (89.4%), ranked 47 | |
| 20 | Security | dispossessed ↓ | 0.92 (1.68) | |

## AM (17: below 20, small-group warning)

| # | Section | Metric | Value | Note |
|---|---|---|---|---|
| 1 ★ | Shooting | npxg (model) | 0.186 (0.277) | |
| 2 ★ | Shooting | shots | 1.92 (2.84) | |
| 3 ★ | Shooting | touches_opp_box | 3.50 (4.99) | |
| 4 | Shooting | xgot (model) | 0.205 (0.408) | May include penalties (unverified) |
| 5 ★ | Creation | chances_created | 1.26 (2.21) | |
| 6 ★ | Creation | xa (model) | 0.124 (0.212) | |
| 7 ★ | Creation | through_balls | 0.32 (0.71) | Highest of any group |
| 8 ★ | Creation | passes_into_box | 1.23 (2.76) | |
| 9 | Creation | sca | 3.04 (4.89) | |
| 10 | Creation | big_chances_created | 0.19 (0.45) | |
| 11 ★ | Carrying | progressive_carries | 1.76 (2.69) | |
| 12 | Carrying | carries_into_box | 0.41 (0.78) | |
| 13 ★ | Carrying | successful dribbles per 90 | new | |
| 14 | Carrying | dribbles_completed (rate) | 42.7% (53.4%), ranked 16 | See decision B |
| 15 ★ | Carrying | xt (model) | 0.104 (0.146) | |
| 16 ★ | Out of possession | recoveries | 3.81 (5.02) | |
| 17 | Out of possession | tackles | 1.28 (2.04) | |
| 18 ★ | Security | dispossessed ↓ | 1.36 (1.88) | |

## W (65)

| # | Section | Metric | Value | Note |
|---|---|---|---|---|
| 1 ★ | Shooting | npxg (model) | 0.170 (0.316) | |
| 2 ★ | Shooting | shots | 1.94 (2.64) | |
| 3 ★ | Shooting | touches_opp_box | 4.15 (6.75) | |
| 4 ★ | Creation | xa (model) | 0.147 (0.242) | |
| 5 ★ | Creation | chances_created | 1.30 (1.98) | |
| 6 | Creation | crosses completed per 90 | new | |
| 7 | Creation | crosses_completed (rate) | 21.4% (29.1%), ranked 60 | |
| 8 ★ | Creation | passes_into_box | 1.38 (2.55) | |
| 9 | Creation | sca | 3.41 (4.78) | |
| 10 ★ | 1v1 & carrying | successful dribbles per 90 | new | The classic winger volume stat |
| 11 ★ | 1v1 & carrying | dribbles_completed (rate) | 44.4% (54.5%), ranked 63 | |
| 12 ★ | 1v1 & carrying | progressive_carries | 2.95 (4.61) | |
| 13 ★ | 1v1 & carrying | carries_into_box | 0.91 (1.91) | |
| 14 ★ | 1v1 & carrying | xt (model) | 0.113 (0.214) | |
| 15 | 1v1 & carrying | progressive_carry_distance | 97.8 (136.3) m | Overlaps 12 |
| 16 ★ | Out of possession | recoveries | 4.02 (4.89) | |
| 17 | Out of possession | tackles | 1.32 (2.32) | |
| 18 | Security | dispossessed ↓ | 1.44 (2.15) | |

## ST (33)

| # | Section | Metric | Value | Note |
|---|---|---|---|---|
| 1 ★ | Shooting | npxg (model) | 0.370 (0.506) | |
| 2 ★ | Shooting | shots | 2.47 (3.23) | |
| 3 ★ | Shooting | touches_opp_box | 4.82 (6.07) | |
| 4 | Shooting | xgot (model) | 0.392 (0.550) | May include penalties (unverified) |
| 5 | Shooting | offsides ↓ | 0.46 (0.77) | Style |
| 6 ★ | Link-up | xa (model) | 0.043 (0.095) | |
| 7 ★ | Link-up | chances_created | 0.70 (1.02) | |
| 8 ★ | Link-up | passes_completed (rate) | 67.4% (76.4%), ranked 26 | Minimum 200 passes; lower to ~100 (decision E) |
| 9 ★ | Link-up | miscontrols ↓ | 2.85 (4.14) | |
| 10 | Link-up | dispossessed ↓ | 1.27 (1.78) | Overlaps 9 |
| 11 ★ | Link-up | sca | 1.61 (2.33) | Includes chances created |
| 12 ★ | Aerial | aerials_won (rate) | 39.5% (51.1%) | |
| 13 ★ | Aerial | aerials won per 90 | new | |
| 14 ★ | Pressing | recoveries | 2.05 (2.80) | |
| 15 ★ | Pressing | tackles | 0.69 (1.22) | |
| 16 | Pressing | defensive_actions | 4.58 (5.94) | Composition unverified |
| 17 | Carrying | progressive_carries | 0.75 (1.33) | |
| 18 | Carrying | successful dribbles per 90 | new | |

---

## Decisions that apply across positions

- **A. Volume next to success rates.** `*_completed` / `*_won` fractions rank the success rate only. Proposed: add count metrics for the successes (dribbles, aerials, crosses, long balls, passes completed), read from the same source fields, so "successful dribbles per 90" and "dribble success %" are separate metrics. Needs an offline renormalize (no API calls).
- **B. Near-duplicate pairs.** dribbles_completed (/players) vs take_ons_completed (advanced): similar eligibility (W 63 vs 64), different definitions (W median 44% vs 36%). key_passes (advanced) vs chances_created (/players): nearly identical medians. Suggested: the /players versions of both, for coverage when advanced analytics are missing (D-012) and because they match FM's columns (Dribbles, Chances Created).
- **C. Overall value models** (VAEP, PV): player card, not templates.
- **D. Template size:** 8–14 metrics, about 12 by default (GK 8).
- **E. Passing minimum:** lower passes_completed's minimum from 200 to 100 attempts (affects every group; mostly lets more strikers in).
- **Known bias, for the backlog:** volume stats depend on team possession (defensive counts inflate for low-possession teams, passing counts for high-possession teams). Possession-adjusted versions are a later option.