import { describe, expect, it } from 'vitest'

import { errorCode, errorMessage, isApiError } from './client'

describe('API errors', () => {
  it('reads the backend envelope', () => {
    const body = { error: { code: 'PLAYER_NOT_FOUND', message: 'no player p_X' } }
    expect(isApiError(body)).toBe(true)
    expect(errorCode(body)).toBe('PLAYER_NOT_FOUND')
    expect(errorMessage(body)).toBe('no player p_X')
  })

  it('explains a network failure and ignores other shapes', () => {
    expect(errorMessage(new TypeError('Failed to fetch'))).toMatch(/scouting serve/)
    expect(isApiError({ detail: 'x' })).toBe(false)
    expect(errorCode(new Error('x'))).toBeNull()
  })
})
