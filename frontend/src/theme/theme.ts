/** Light / dark theme: a stored choice wins, otherwise the system setting (index.html applies
 * the same rule before the first paint). Colours live in tokens.css as CSS variables. */
import { useCallback, useEffect, useState } from 'react'

export type ThemeMode = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'scout.theme'

export function initialTheme(stored: string | null, prefersDark: boolean): ThemeMode {
  if (stored === 'light' || stored === 'dark') return stored
  return prefersDark ? 'dark' : 'light'
}

function readStored(): string | null {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY)
  } catch {
    return null // storage can be blocked (privacy settings); fall back to the system setting
  }
}

function prefersDark(): boolean {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

export function useTheme(): { mode: ThemeMode; toggle: () => void } {
  const [mode, setMode] = useState<ThemeMode>(() => initialTheme(readStored(), prefersDark()))

  useEffect(() => {
    document.documentElement.dataset.theme = mode
  }, [mode])

  const toggle = useCallback(() => {
    setMode((current) => {
      const next: ThemeMode = current === 'dark' ? 'light' : 'dark'
      try {
        window.localStorage.setItem(THEME_STORAGE_KEY, next)
      } catch {
        // not remembered across visits; the toggle still works for this one
      }
      return next
    })
  }, [])

  return { mode, toggle }
}
