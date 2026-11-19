# PITCHAPI_NOTES.md

Verified facts about PitchAPI that this project depends on. Written in our own words; the official docs are the authority.

**Sources:**
- The official docs (PDF export, changelog current to Nov 14, 2026)
- The source of the Python SDK `pitchapi` 0.1.6 (MIT license)

**Last checked:** 2026-11-19

## Basics

| | |
|---|---|
| Base URL | `https://api.pitchapi.dev`. All paths are under `/v1`. Plain HTTP is refused, not redirected |
| Auth | `X-API-KEY` header. Keys start with `pk_live_` or `pk_test_` |
| SDK key variable | `PITCHAPI_API_KEY`, the same name as our `.env.example` |
| Envelope | `{"data": …}` on success or `{"error": {"code", "message"}}` on failure, never both. Branch on `code` |
| Limits | No per-second or per-day allowance. There is only an unpublished burst guard, which returns 429 `RATE_LIMIT_EXCEEDED` with `Retry-After` |
| Coverage | 77 leagues, history back to 2021 |
| IDs | Prefix + `_` + 6 base62 characters (`m_`, `p_`, `t_`, `l_`, `s_`). Opaque and stable. Shot IDs are unique only within their match |

## Endpoints we plan to use

| Endpoint | Used for | Phase |
|---|---|---|
| `GET /v1/leagues` | League IDs, and the seasons available for each league | 1 |
| `GET /v1/leagues/{id}/matches?season=&status=` | Match list for a season | 1 |
| `GET /v1/matches/{id}/players` | Per-match player stat lines | 1 |
| `GET /v1/matches/{id}/advanced/players` | xT, VAEP, progressive passes and carries, SCA/GCA, xAG, defending, goalkeeping | 1 |
| `GET /v1/matches/{id}/lineups` | Per-match position (see below) | 1 |
| `GET /v1/players/{id}` | Profile: full name, short name, shirt number, `position_id`, country, image | 1 |
| `GET /v1/teams/{id}` | Team name and image | 1 |
| `GET /v1/matches/{id}/shots`, `/heatmaps`, `/advanced/network` | Rich visuals | 8 |
| `GET /v1/matches/{id}/advanced`, `/stats` | Team pages | 5 |

**Not used:**
- `/live`. It exists (added Nov 14, 2026, limited to 60 requests/minute), but we work from post-match data only (D-005).
- `/players/halves`.

## Seasons and match lists

- **Season strings:** `"2025/2026"` for leagues that run autumn to spring, and `"2024"` for calendar-year leagues such as MLS. Take them from the `seasons` array in `/v1/leagues`; don't build them yourself.
- **The `status` filter:** `played` (the default), `upcoming` or `all`. Upcoming matches have `status: "not_started"` and a null score.
- **Match IDs are stable** from fixture through to result. A played match fills in its full data after the final whistle.

## Player stat lines (`/matches/{id}/players`)

- **Grouped stats.** Each row is `player` + `team_id` + `stats[]` groups, and each group holds entries.
  - Read each entry by its inner `key`, e.g. `"goals"`. **Never use the display label**, e.g. `"Goals"`; labels can change.
  - Groups:
    - `top_stats`: every player who featured.
    - `attack`, `defense`, `duels`: outfield players only.
    - `physical_metrics`: rarely present.
- **Branch on `stat.type`:**

  | Type | Holds |
  |---|---|
  | `integer`, `double` | `value` |
  | `fractionWithPercentage` | `value` out of `total` |
  | `distance`, `distanceWithPercentage`, `speed` | Physical measurements |
  | `fantasyPoints` | A fantasy score |
  | `boolean` | A presence flag. **The docs say to skip it** |

- **Minutes.** `minutes_played` is in `top_stats`, and also sits at the top level in the advanced endpoint.
- **Rating** (key `rating_title`) is match-level only. **It's missing entirely** in the 26 leagues that were rebuilt in Sept 2026; none of them is in the top 5.
- **Optional fields are omitted** when unavailable. A null is never a zero: for a rate, it means the denominator was empty.

## Advanced player analytics (`/matches/{id}/advanced/players`)

- **Groups:** `possession_value`, `passing`, `carrying`, `creation`, `defending`, `goalkeeping`, plus top-level `minutes_played` and `actions`.
- **Model outputs** (flag these in the UI): xT, VAEP, PV and xAG; xG chain and xG buildup are built on shot xG.
- **Carries are synthesized.** The source has no carry event; one is inserted between consecutive actions over 5 m.
- **Goalkeeping** is present only for the two starting keepers and null for everyone else. A quiet keeper has zeros, not nulls.
- **Don't double-count:**
  - `passing.passes` already includes `passing.crosses`.
  - `creation.chances_created` is published as the same number as `passing.key_passes`.
- **⚠️ The docs contradict themselves on `chances_created`.** They say it equals `key_passes`, but they also define it as "assists plus key passes". Check this against real data in the spike.
- **SCA sums short.** `sca_breakdown.foul_drawn` belongs to the team only, so the players' SCA adds up to less than the team total.
- **Unrated matches.** `ANALYTICS_UNAVAILABLE` (404) means the match exists but was never rated. That's normal, not an error.

## Positions (unresolved: decides peer groups)

- **`/matches/{id}/players`.** `player.position_id` is often `null` in the list response.
- **`/matches/{id}/players/{pid}`.** The per-player detail has a `position_id` (the example shows `4`).
- **`/players/{id}`.** The profile has a `position_id` (the example shows `4` for a striker). **The docs don't say what these numbers mean.**
- **`/lineups`.** `position_id` means something different for starters and substitutes:
  - **Starter:** a slot in the formation grid. Each starter also has `pitch_x` and `pitch_y` from 0 to 1.
  - **Substitute:** a category. `0` = GK, `1` = DEF, `2` = MID, `3` = ATT, `-1` = unclassified. Both pitch coordinates are 0.
- **Likely approach** (confirm with the spike):
  - Assign each starter a role from the formation and its `pitch_x`/`pitch_y`.
  - Give substitutes their broad category.
  - A player's season position is then their most-played role, weighted by minutes.
  - The profile `position_id` is a fallback only if the spike lets us work out what its numbers mean.

## Errors

| Status | Code | What we do |
|---|---|---|
| 400 | `INVALID_PARAMETER` | Bug on our side: fail loudly |
| 401 | `UNAUTHORIZED` | Bad or missing key: abort the sync |
| 404 | `RESOURCE_NOT_FOUND` | The ID doesn't exist. Log it. For per-player advanced data, it means the player took no part in the match |
| 404 | `ANALYTICS_UNAVAILABLE` | Negative-cache it with a re-check date; mark the match "not rated" |
| 404 | `HALVES_UNAVAILABLE` | Not used |
| 429 | `RATE_LIMIT_EXCEEDED` | Wait for `Retry-After`, then retry |
| 500 | `INTERNAL_SERVER_ERROR` | Back off and retry |

The SDK also maps `SUBSCRIPTION_SUSPENDED` and `PLAN_UPGRADE_REQUIRED`, which are left over from the old paid tier.

## SDK 0.1.6 caveats (from reading its source)

- **Its dataclass models default missing counts to `0`** (e.g. `passes: int = 0`). Parsing through them breaks null ≠ 0. **We parse raw JSON with our own Pydantic models.**
- **Raw `data` is only reachable through the private `client._get(path, params)`.** The public methods convert everything to dataclasses.
- **Retries:** up to 2 by default, on 429/500/502/503/504 and network errors. It honours `Retry-After` exactly; otherwise it backs off exponentially with jitter, capped at 8 s.

## Other things to know

- **Team stats are display strings.** `/matches/{id}/stats` returns values like `"12 (55%)"`, to be parsed according to `format_type`. Use `/advanced` when you need typed numbers.
- **Pitch coordinates:**
  - Shots are in metres on a 105 × 68 pitch, normalized so the attack always goes towards x = 105.
  - Lineup coordinates run 0–1, not metres.
- **Heatmaps:**
  - A 16 × 12 grid of sparse `[cell_x, cell_y, actions]` triples; an absent cell means no actions there.
  - The default frame (`acting_ltr`) shows each team attacking in its own direction. Use `frame=home_ltr` to put both teams on one pitch.
- **IDs changed in Sept 2026 for the 26 rebuilt leagues.** Matters only if we ever cache those leagues from before that date.

## Still unknown (the Phase 0 spike settles these)

- [ ] What profile and detail `position_id` numbers mean
- [ ] How starter formation slots relate to `pitch_x`/`pitch_y`; check across several formations
- [ ] Whether `chances_created` equals `key_passes` or `key_passes + assists` in real data
- [ ] The full set of stat `key`s per group for EPL matches
- [ ] Whether advanced analytics ever appear later for a match that returned `ANALYTICS_UNAVAILABLE`
- [ ] **Terms of use and data license.** The docs don't include them. Ask PitchAPI support before hosting (D-007)
