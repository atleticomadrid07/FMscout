import { describe, expect, it } from 'vitest'

import { initialTheme } from './theme'

describe('initialTheme', () => {
  it('uses a stored choice first, then the system setting', () => {
    expect(initialTheme('dark', false)).toBe('dark')
    expect(initialTheme('light', true)).toBe('light')
    expect(initialTheme(null, true)).toBe('dark')
    expect(initialTheme('purple', false)).toBe('light')
  })
})
