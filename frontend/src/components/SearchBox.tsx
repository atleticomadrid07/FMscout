/** Player search in the top bar: type 2+ letters, pick with the mouse or ↑ ↓ Enter.
 * Enter with nothing highlighted opens the full results page. Accessible as an ARIA combobox. */
import { keepPreviousData } from '@tanstack/react-query'
import { useId, useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router'

import { $api, errorMessage } from '../api/client'
import { useDebounced } from '../lib/useDebounced'
import { withScope } from '../state/scope'
import styles from './SearchBox.module.css'

const MIN_LENGTH = 2
const LIMIT = 8
export const SEARCH_DEBOUNCE_MS = 250

export function SearchBox({ scope }: { scope: string | undefined }) {
  const navigate = useNavigate()
  const listId = useId()
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const q = useDebounced(text.trim(), SEARCH_DEBOUNCE_MS)
  const enabled = scope !== undefined && q.length >= MIN_LENGTH

  const search = $api.useQuery(
    'get',
    '/api/v1/players/search',
    { params: { query: { scope: scope ?? '', q, limit: LIMIT } } },
    { enabled, placeholderData: keepPreviousData },
  )
  const hits = enabled ? (search.data?.results ?? []) : []
  const showList = open && text.trim().length >= MIN_LENGTH

  function reset() {
    setText('')
    setOpen(false)
    setActive(-1)
  }

  function openPlayer(id: string) {
    reset()
    navigate(withScope(`/players/${id}`, scope))
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setOpen(true)
      setActive((i) => (hits.length ? (i + 1) % hits.length : -1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((i) => (hits.length ? (i <= 0 ? hits.length - 1 : i - 1) : -1))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const hit = hits[active]
      if (hit) openPlayer(hit.id)
      else if (text.trim().length >= MIN_LENGTH) {
        const query = text.trim()
        reset()
        navigate(withScope('/players', scope, { q: query }))
      }
    } else if (event.key === 'Escape') {
      setOpen(false)
      setActive(-1)
    }
  }

  const activeId = active >= 0 && hits[active] ? `${listId}-${active}` : undefined

  return (
    <div className={styles.wrap}>
      <label className="visually-hidden" htmlFor={`${listId}-input`}>
        Search players
      </label>
      <input
        id={`${listId}-input`}
        className={styles.input}
        type="search"
        placeholder={scope ? 'Search players…' : 'Loading seasons…'}
        disabled={!scope}
        autoComplete="off"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={activeId}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setOpen(true)
          setActive(-1)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {showList && (
        <ul id={listId} role="listbox" className={styles.list} aria-label="Players">
          {search.isError && <li className={styles.note}>{errorMessage(search.error)}</li>}
          {!search.isError && enabled && search.isFetched && hits.length === 0 && (
            <li className={styles.note}>No players match “{q}”.</li>
          )}
          {!search.isError && (!enabled || (!search.isFetched && hits.length === 0)) && (
            <li className={styles.note}>Searching…</li>
          )}
          {hits.map((hit, index) => (
            <li
              key={hit.id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              className={index === active ? `${styles.option} ${styles.active}` : styles.option}
              // mousedown, not click: it fires before the input's blur closes the list
              onMouseDown={(e) => {
                e.preventDefault()
                openPlayer(hit.id)
              }}
              onMouseEnter={() => setActive(index)}
            >
              <span className={styles.name}>{hit.name}</span>
              <span className={styles.meta}>
                {hit.team ?? '—'}
                {hit.peer_role && <span className={styles.role}>{hit.peer_role}</span>}
                <span>{hit.minutes} min</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
