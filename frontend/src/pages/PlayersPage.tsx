import { keepPreviousData } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'

import { $api } from '../api/client'
import { ErrorNote, Loading } from '../components/Status'
import { useDebounced } from '../lib/useDebounced'
import { useScope, withScope } from '../state/scope'
import styles from './Page.module.css'

const ROLES = ['GK', 'CB', 'FB', 'DM', 'CM', 'AM', 'W', 'ST'] as const

/** Full search results with filters; the URL keeps q, role, team and minutes. */
export function PlayersPage() {
  const { key } = useScope()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const role = params.get('role') ?? ''
  const team = params.get('team') ?? ''
  const minMinutes = Number(params.get('min') ?? 0) || 0
  const debouncedQ = useDebounced(q.trim(), 250)
  const debouncedTeam = useDebounced(team.trim(), 250)
  const enabled = key !== undefined && debouncedQ.length >= 2

  const results = $api.useQuery(
    'get',
    '/api/v1/players/search',
    {
      params: {
        query: {
          scope: key ?? '',
          q: debouncedQ,
          role: role || undefined,
          team: debouncedTeam || undefined,
          min_minutes: minMinutes,
          limit: 50,
        },
      },
    },
    { enabled, placeholderData: keepPreviousData },
  )

  function set(name: string, value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current)
      if (value) next.set(name, value)
      else next.delete(name)
      return next
    })
  }

  return (
    <div className={styles.page}>
      <h1>Players</h1>
      <div className={styles.filters}>
        <label>
          Name
          <input type="search" value={q} onChange={(e) => set('q', e.target.value)} autoFocus />
        </label>
        <label>
          Position
          <select value={role} onChange={(e) => set('role', e.target.value)}>
            <option value="">Any</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <label>
          Team
          <input value={team} onChange={(e) => set('team', e.target.value)} />
        </label>
        <label>
          Min. minutes
          <input
            type="number"
            min={0}
            step={90}
            value={minMinutes || ''}
            onChange={(e) => set('min', e.target.value)}
          />
        </label>
      </div>
      {!enabled && <p className={styles.muted}>Type at least two letters of a name.</p>}
      {enabled && results.isPending && <Loading label="Searching…" />}
      {results.isError && <ErrorNote error={results.error} />}
      {enabled && results.data && results.data.results.length === 0 && (
        <p className={styles.muted}>No players match.</p>
      )}
      {enabled && results.data && results.data.results.length > 0 && (
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Player</th>
              <th>Team</th>
              <th>Position</th>
              <th className={styles.num}>Minutes</th>
              <th className={styles.num} title="How closely the name matches (100 = exact)">
                Name match
              </th>
            </tr>
          </thead>
          <tbody>
            {results.data.results.map((hit) => (
              <tr key={hit.id}>
                <td>
                  <Link to={withScope(`/players/${hit.id}`, key)}>{hit.name}</Link>
                </td>
                <td>{hit.team ?? '—'}</td>
                <td>{hit.peer_role ?? <span className={styles.muted}>no role</span>}</td>
                <td className={styles.num}>{hit.minutes}</td>
                <td className={styles.num}>{hit.exact ? 'exact' : Math.round(hit.score)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
