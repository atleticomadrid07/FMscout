import styles from './Page.module.css'

export function Placeholder({ title, slice }: { title: string; slice: string }) {
  return (
    <div className={styles.page}>
      <h1>{title}</h1>
      <p className={styles.muted}>Coming in slice {slice}.</p>
    </div>
  )
}

export function NotFound() {
  return (
    <div className={styles.page}>
      <h1>Page not found</h1>
      <p className={styles.muted}>Use the search or the menu on the left.</p>
    </div>
  )
}
