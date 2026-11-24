import { useScope } from '../state/scope'
import styles from './TopBar.module.css'

/** Season select in the top bar; the choice goes into the URL (`?s=`). */
export function SeasonPicker() {
  const { key, scopes, setScope } = useScope()
  if (scopes.length === 0) return null
  return (
    <label className={styles.season}>
      <span className="visually-hidden">Season</span>
      <select value={key ?? ''} onChange={(e) => setScope(e.target.value)}>
        {scopes.map((s) => (
          <option key={s.key} value={s.key} disabled={!s.aggregated}>
            {s.competition} {s.season}
            {s.aggregated ? '' : ' (not aggregated)'}
          </option>
        ))}
      </select>
    </label>
  )
}
