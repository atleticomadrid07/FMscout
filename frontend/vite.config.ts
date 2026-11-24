import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The API (`uv run scouting serve`) listens on 127.0.0.1:8000. In development the browser only
// talks to Vite (localhost:5173), which forwards /api there: same origin, no CORS needed.
const API_TARGET = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: API_TARGET, changeOrigin: false } },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
})
