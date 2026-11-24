import { useState } from 'react'
import { Outlet } from 'react-router'

import { useTheme } from '../theme/theme'
import styles from './AppShell.module.css'
import { HealthBanner } from './HealthBanner'
import { SideMenu } from './SideMenu'
import { TopBar } from './TopBar'

/** Top bar (search, season, theme) + left menu + the page. */
export function AppShell() {
  const { mode, toggle } = useTheme()
  const [menuOpen, setMenuOpen] = useState(false)
  return (
    <div className={styles.shell}>
      <TopBar theme={mode} onToggleTheme={toggle} onToggleMenu={() => setMenuOpen((o) => !o)} />
      <div className={styles.body}>
        <SideMenu open={menuOpen} onNavigate={() => setMenuOpen(false)} />
        <main className={styles.main}>
          <HealthBanner />
          <Outlet />
        </main>
      </div>
    </div>
  )
}
