import { $api, errorMessage } from '../api/client'
import styles from './Status.module.css'

/** A banner when the API is down or its database needs a migration; nothing otherwise. */
export function HealthBanner() {
  const health = $api.useQuery('get', '/api/v1/health', {}, { refetchInterval: 30_000 })
  if (health.isError) {
    return (
      <div role="alert" className={`${styles.banner} ${styles.danger}`}>
        {errorMessage(health.error)}
      </div>
    )
  }
  if (health.data?.status === 'schema_out_of_date') {
    return (
      <div role="alert" className={`${styles.banner} ${styles.warn}`}>
        The database needs a migration ({health.data.message}). Run{' '}
        <code>uv run alembic upgrade head</code>, then restart <code>scouting serve</code>.
      </div>
    )
  }
  return null
}
