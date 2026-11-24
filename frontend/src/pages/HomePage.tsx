import { useScope } from '../state/scope'
import { ErrorNote, Loading } from '../components/Status'
import styles from './Page.module.css'

/** For now: what season is loaded and how complete it is. Top players per position come in 4f. */
export function HomePage() {
  const { scope, query } = useScope()
  if (query.isPending) return <Loading />
  if (query.isError) return <ErrorNote error={query.error} />
  if (!scope) {
    return (
      <p className={styles.muted}>
        No aggregated season yet. Run <code>uv run scouting sync season …</code> and{' '}
        <code>scouting aggregate</code>.
      </p>
    )
  }
  const c = scope.coverage
  return (
    <div className={styles.page}>
      <h1>
        {scope.competition} {scope.season}
      </h1>
      <p className={styles.lead}>
        Search a player in the top bar. Data: PitchAPI, aggregated {formatDate(scope.computed_at)}.
      </p>
      <section className={styles.card} aria-labelledby="coverage">
        <h2 id="coverage">Coverage</h2>
        <dl className={styles.facts}>
          <Fact label="Matches" value={c.matches} />
          <Fact label="With ratings" value={c.matches_with_ratings} />
          <Fact label="No advanced analytics" value={c.advanced_unavailable} />
          <Fact label="No heatmaps" value={c.heatmaps_missing} />
          <Fact label="Not yet synced" value={c.pending} />
        </dl>
      </section>
      <section className={styles.card} aria-labelledby="groups">
        <h2 id="groups">Peer groups</h2>
        <p className={`${styles.muted} ${styles.small}`}>
          Percentiles compare a player with his position group: players with at least{' '}
          {scope.min_minutes ?? '—'} minutes this season.
        </p>
        <ul className={styles.chips}>
          {scope.peer_groups.map((g) => (
            <li
              key={g.role}
              className={g.small_sample ? `${styles.chip} ${styles.chipWarn}` : styles.chip}
              title={g.small_sample ? 'Small sample: under 20 players' : undefined}
            >
              {g.role} · {g.size}
              {g.small_sample && ' ⚠'}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

function Fact({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}

function formatDate(iso: string | null): string {
  if (!iso) return 'never'
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' })
}
