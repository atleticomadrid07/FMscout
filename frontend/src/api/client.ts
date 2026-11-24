/**
 * The typed API client. `schema.d.ts` is generated from the backend's committed OpenAPI
 * snapshot (`npm run gen:api`), so every path, parameter and response below is checked
 * against the real contract: if the backend changes shape, this stops compiling.
 */
import createFetchClient from 'openapi-fetch'
import createClient from 'openapi-react-query'

import type { components, paths } from './schema'

export type Schemas = components['schemas']

export const fetchClient = createFetchClient<paths>({
  // Same origin: Vite proxies /api to the backend in development (vite.config.ts). Absolute,
  // because a `Request` built from a bare path fails outside a browser (tests run in Node).
  baseUrl: globalThis.location?.origin ?? '',
  // Look `fetch` up per request (not once at import) so tests can stub it.
  fetch: (request) => globalThis.fetch(request),
})

/** TanStack Query hooks: `$api.useQuery('get', '/api/v1/scopes')`. */
export const $api = createClient(fetchClient)

/** The backend's error envelope: `{"error": {"code", "message"}}`. */
export type ApiErrorBody = Schemas['ErrorResponse']

export function isApiError(error: unknown): error is ApiErrorBody {
  if (typeof error !== 'object' || error === null || !('error' in error)) return false
  const inner = (error as { error: unknown }).error
  return typeof inner === 'object' && inner !== null && 'code' in inner && 'message' in inner
}

/** The API's error code, or null for a network failure / anything else. */
export function errorCode(error: unknown): string | null {
  return isApiError(error) ? error.error.code : null
}

/** One line for the user. Network failures usually mean the API isn't running. */
export function errorMessage(error: unknown): string {
  if (isApiError(error)) return error.error.message
  if (error instanceof TypeError) return "Can't reach the API. Is `uv run scouting serve` running?"
  if (error instanceof Error) return error.message
  return 'Something went wrong.'
}
