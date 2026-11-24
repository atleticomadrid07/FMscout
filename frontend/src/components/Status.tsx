import { errorCode, errorMessage } from '../api/client'
import styles from './Status.module.css'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p className={styles.muted} role="status">
      {label}
    </p>
  )
}

export function ErrorNote({ error }: { error: unknown }) {
  const code = errorCode(error)
  return (
    <div role="alert" className={`${styles.banner} ${styles.danger}`}>
      {errorMessage(error)}
      {code && <code className={styles.code}>{code}</code>}
    </div>
  )
}
