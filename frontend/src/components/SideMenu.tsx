import { NavLink } from 'react-router'

import { useScope, withScope } from '../state/scope'
import { SeasonPicker } from './SeasonPicker'
import styles from './SideMenu.module.css'

const ITEMS = [
  { to: '/', label: 'Home', end: true },
  { to: '/players', label: 'Players', end: false },
  { to: '/groups', label: 'Groups', end: false },
  { to: '/compare', label: 'Compare', end: false },
] as const

export function SideMenu({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const { key } = useScope()
  return (
    <nav className={open ? `${styles.menu} ${styles.open}` : styles.menu} aria-label="Main">
      {/* The top bar hides its season picker on narrow screens; it lives here instead. */}
      <div className={styles.narrowOnly}>
        <SeasonPicker />
      </div>
      <ul>
        {ITEMS.map((item) => (
          <li key={item.to}>
            <NavLink
              to={withScope(item.to, key)}
              end={item.end}
              className={({ isActive }) =>
                isActive ? `${styles.link} ${styles.active}` : styles.link
              }
              onClick={onNavigate}
            >
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
      <p className={styles.section}>Football Manager</p>
      <ul>
        <li>
          <span className={`${styles.link} ${styles.disabled}`} aria-disabled="true">
            FM imports <small>(later)</small>
          </span>
        </li>
      </ul>
    </nav>
  )
}
