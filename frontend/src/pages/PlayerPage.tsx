import { useParams } from 'react-router'

import { $api } from '../api/client'
import { ErrorNote, Loading } from '../components/Status'
import { useScope } from '../state/scope'
import styles from './Page.module.css'

/** Placeholder for slice 4a: proves routing and the typed API call. The full page is 4b. */
export function PlayerPage() {
  const { id = '' } = useParams()
  const { key } = useScope()
  const player = $api.useQuery(
    'get',
    '/api/v1/players/{player_id}',
    { params: { path: { player_id: id }, query: { scope: key ?? '' } } },
    { enabled: key !== undefined && id !== '' },
  )
  if (!key || player.isPending) return <Loading />
  if (player.isError) return <ErrorNote error={player.error} />
  const p = player.data
  const team = p.teams.map((t) => t.name).join(', ') || '—'
  return (
    <div className={styles.page}>
      <h1>{p.name}</h1>
      <p className={styles.lead}>
        {team} · {p.peer_role ?? 'no position group'} · {p.minutes} min in {p.matches} matches ·{' '}
        {p.provenance.competition} {p.provenance.season}
      </p>
      <p className={styles.muted}>The full player page (card, stat table) arrives in slice 4b.</p>
    </div>
  )
}
