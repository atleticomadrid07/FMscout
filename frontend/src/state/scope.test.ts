import { describe, expect, it } from 'vitest'

import type { Schemas } from '../api/client'
import { pickDefaultScope, withScope } from './scope'

// Shaped like /api/v1/scopes; values are made up for the test (no real data).
function scope(key: string, season: string, opts: Partial<Schemas['ScopeOut']> = {}) {
  return {
    key,
    season,
    competition: 'Test League',
    country_code: 'XX',
    kind: 'season',
    source: 'pitchapi',
    aggregated: true,
    is_current: false,
    computed_at: null,
    min_minutes: 855,
    peer_groups: [],
    coverage: {
      matches: 0,
      matches_with_ratings: 0,
      advanced_unavailable: 0,
      heatmaps_missing: 0,
      lineups_missing: 0,
      players_missing: 0,
      pending: 0,
    },
    ...opts,
  } satisfies Schemas['ScopeOut']
}

describe('pickDefaultScope', () => {
  it('prefers the current season, then the latest, and skips unaggregated ones', () => {
    const old = scope('l_A.2023-2024', '2023/2024')
    const latest = scope('l_A.2024-2025', '2024/2025')
    const current = scope('l_A.2025-2026', '2025/2026', { is_current: true, aggregated: false })
    expect(pickDefaultScope([old, latest, current])?.key).toBe('l_A.2024-2025')
    const ready = { ...current, aggregated: true }
    expect(pickDefaultScope([old, ready, latest])?.key).toBe('l_A.2025-2026')
    expect(pickDefaultScope([])).toBeUndefined()
  })
})

describe('withScope', () => {
  it('adds the season and drops empty params', () => {
    expect(withScope('/players/p_1', 'l_A.2025-2026')).toBe('/players/p_1?s=l_A.2025-2026')
    expect(withScope('/players', 'l_A.2025-2026', { q: 'e. haaland', role: '' })).toBe(
      '/players?q=e.+haaland&s=l_A.2025-2026',
    )
    expect(withScope('/', undefined)).toBe('/')
  })
})
