import { Link } from 'react-router'

import { withScope, useScope } from '../state/scope'
import type { ThemeMode } from '../theme/theme'
import { SearchBox } from './SearchBox'
import { SeasonPicker } from './SeasonPicker'
import styles from './TopBar.module.css'

type Props = {
  theme: ThemeMode
  onToggleTheme: () => void
  onToggleMenu: () => void
}

export function TopBar({ theme, onToggleTheme, onToggleMenu }: Props) {
  const { key } = useScope()
  return (
    <header className={styles.bar}>
      <button
        type="button"
        className={styles.menuButton}
        onClick={onToggleMenu}
        aria-label="Toggle menu"
      >
        ☰
      </button>
      <Link to={withScope('/', key)} className={styles.brand}>
        ⚽ Scout
      </Link>
      <div className={styles.search}>
        <SearchBox scope={key} />
      </div>
      <SeasonPicker />
      <button
        type="button"
        className={styles.iconButton}
        onClick={onToggleTheme}
        aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
        title={theme === 'dark' ? 'Light theme' : 'Dark theme'}
      >
        {theme === 'dark' ? '☀' : '☾'}
      </button>
    </header>
  )
}
