import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SearchBox } from './SearchBox'

// The backend's hand-made fixture season (tests/fixtures/pitchapi), not real data.
const SCOPE = 'l_FIXT01.2025-2026'
const RESULTS = {
  query: 'cal',
  scope: SCOPE,
  results: [
    { id: 'p_CST01', name: 'Cal Striker', short_name: 'C. Striker', team: 'Placeholder Town',
      peer_role: 'ST', minutes: 156, score: 90, exact: false },
    { id: 'p_AST01', name: 'Cal Striker', short_name: 'C. Striker', team: 'Example Athletic',
      peer_role: 'ST', minutes: 78, score: 90, exact: false },
  ],
} // prettier-ignore

function Where() {
  const location = useLocation()
  return <output data-testid="where">{location.pathname + location.search}</output>
}

function setup() {
  const fetchMock = vi.fn(async (request: Request) => {
    const url = new URL(request.url)
    expect(url.pathname).toBe('/api/v1/players/search')
    return new Response(JSON.stringify({ ...RESULTS, query: url.searchParams.get('q') }), {
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>
        <SearchBox scope={SCOPE} />
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { fetchMock, input: screen.getByRole('combobox') }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('SearchBox', () => {
  it('searches after two letters and opens the highlighted player', async () => {
    const user = userEvent.setup()
    const { fetchMock, input } = setup()
    await user.type(input, 'c')
    expect(screen.queryByRole('option')).toBeNull()
    await user.type(input, 'al')
    const options = await screen.findAllByRole('option')
    expect(options).toHaveLength(2)
    expect(options[0]?.textContent).toContain('Placeholder Town')
    const lastRequest = fetchMock.mock.calls.at(-1)?.[0]
    expect(lastRequest).toBeDefined()
    const sent = new URL((lastRequest as Request).url)
    expect(sent.searchParams.get('scope')).toBe(SCOPE)
    expect(sent.searchParams.get('q')).toBe('cal')

    await user.keyboard('{ArrowDown}{ArrowDown}')
    expect(input.getAttribute('aria-activedescendant')).toBe(options[1]?.id)
    await user.keyboard('{Enter}')
    expect(screen.getByTestId('where').textContent).toBe(`/players/p_AST01?s=${SCOPE}`)
    expect((input as HTMLInputElement).value).toBe('')
  })

  it('opens the results page on Enter with nothing highlighted', async () => {
    const user = userEvent.setup()
    const { input } = setup()
    await user.type(input, 'cal')
    await screen.findAllByRole('option')
    await user.keyboard('{Enter}')
    await waitFor(() =>
      expect(screen.getByTestId('where').textContent).toBe(`/players?q=cal&s=${SCOPE}`),
    )
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    const { input } = setup()
    await user.type(input, 'cal')
    await screen.findAllByRole('option')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(input.getAttribute('aria-expanded')).toBe('false')
  })
})
