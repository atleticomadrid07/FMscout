# Scout frontend

The web UI (Phase 4): Vite + React + TypeScript (strict), TanStack Query, typed API client
generated from the backend's OpenAPI snapshot. Design: `claude/PHASE4_UI.md` in the project.

## Run it

Needs Node `^20.19` or `>=22.12` (`node -v`). Two terminals:

```bash
cd backend && uv run scouting serve
```

```bash
cd frontend && npm ci && npm run dev
```

Open http://localhost:5173. Vite forwards `/api` to the API on `127.0.0.1:8000`, so the browser
only ever talks to its own origin.

## Scripts

| Command | Does |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm test` | Vitest (unit + component tests) |
| `npm run typecheck` | `tsc -b`, strict |
| `npm run lint` | oxlint |
| `npm run format` | Prettier |
| `npm run gen:api` | Regenerate `src/api/schema.d.ts` from `backend/tests/unit/api/openapi.json` (after any backend contract change; then fix what stops compiling) |
| `npm run build` | Type-check and build to `dist/` |

## Layout

`src/api` (generated types + client) · `src/theme` (tokens, light/dark) · `src/state` (season in
the URL) · `src/components` (shell, search) · `src/pages`. Every colour is a CSS variable in
`src/theme/tokens.css`; components never hard-code one.
