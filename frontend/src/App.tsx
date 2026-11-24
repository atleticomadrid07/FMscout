import { Route, Routes } from 'react-router'

import { AppShell } from './components/AppShell'
import { HomePage } from './pages/HomePage'
import { NotFound, Placeholder } from './pages/Placeholder'
import { PlayerPage } from './pages/PlayerPage'
import { PlayersPage } from './pages/PlayersPage'

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
        <Route path="players" element={<PlayersPage />} />
        <Route path="players/:id" element={<PlayerPage />} />
        <Route path="groups" element={<Placeholder title="Groups" slice="4d" />} />
        <Route path="compare" element={<Placeholder title="Compare" slice="4e" />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
