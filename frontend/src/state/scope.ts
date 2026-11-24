/** The season every page works in ("scope", e.g. l_4WFCIZ.2025-2026). It lives in the URL as
 * `?s=` so views are bookmarkable; without it, the newest aggregated season is used. */
import { useCallback } from 'react'
import { useSearchParams } from 'react-router'

import { $api, type Schemas } from '../api/client'

export const SCOPE_PARAM = 's'

type Scope = Schemas['ScopeOut']

/** The default season: aggregated ones only (others have no stats yet); the current season
 * first, then the latest. */
export function pickDefaultScope(scopes: readonly Scope[]): Scope | undefined {
  const ready = scopes.filter((s) => s.aggregated)
  return [...ready].sort(
    (a, b) => Number(b.is_current) - Number(a.is_current) || b.season.localeCompare(a.season),
  )[0]
}

/** A link that keeps the season: withScope('/players/p_1', 'l_X.2025-2026', {q: 'a'}). */
export function withScope(
  path: string,
  scope: string | undefined,
  params: Record<string, string | number | undefined> = {},
): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  if (scope) query.set(SCOPE_PARAM, scope)
  const text = query.toString()
  return text ? `${path}?${text}` : path
}

export function useScope() {
  const [params, setParams] = useSearchParams()
  const scopes = $api.useQuery('get', '/api/v1/scopes')
  const list = scopes.data?.scopes ?? []
  const requested = params.get(SCOPE_PARAM)
  const known = list.find((s) => s.key === requested)
  const scope = known ?? pickDefaultScope(list)

  const setScope = useCallback(
    (key: string) => {
      setParams((current) => {
        const next = new URLSearchParams(current)
        next.set(SCOPE_PARAM, key)
        return next
      })
    },
    [setParams],
  )

  return {
    scope, // the ScopeOut in use, or undefined while loading / when nothing is aggregated
    key: scope?.key,
    scopes: list,
    unknownRequested: requested !== null && list.length > 0 && known === undefined,
    query: scopes,
    setScope,
  }
}
