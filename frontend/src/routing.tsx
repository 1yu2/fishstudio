import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import AppShell from './components/shell/AppShell'
import LibraryPage from './components/workspace/LibraryPage'
import ProjectsPage from './components/projects/ProjectsPage'
import SettingsPage from './components/SettingsPage'
import WorkspacePage from './components/workspace/WorkspacePage'
import { paths } from './routePaths'

const NodeCanvasPage = lazy(() => import('./components/nodeCanvas/NodeCanvasPage'))

export function legacyRedirectFor(pathname: string, search: string): string | null {
  if (pathname !== '/') return null

  const params = new URLSearchParams(search)
  const page = params.get('page')
  if (page === 'settings') return paths.settings
  // 旧的漫剧工作台与自由画布已下线，旧链接回到项目列表
  if (page === 'studio') return paths.projects

  return params.get('canvasId') ? paths.projects : null
}

export function LegacyUrlRedirect() {
  const location = useLocation()
  const target = legacyRedirectFor(location.pathname, location.search)
  return target ? <Navigate to={target} replace /> : null
}

type ThemeMode = 'dark' | 'light'

interface AppRoutesProps {
  theme: ThemeMode
  onToggleTheme: () => void
}

const pageFallback = (
  <div style={{ padding: 24, color: 'var(--text-secondary, #6b7280)' }}>加载中…</div>
)

function NodeCanvasRoute() {
  const { canvasId = '' } = useParams()
  return (
    <Suspense fallback={pageFallback}>
      <NodeCanvasPage canvasId={canvasId} />
    </Suspense>
  )
}

export function AppRoutes({ theme, onToggleTheme }: AppRoutesProps) {
  return (
    <>
      <LegacyUrlRedirect />
      <Routes>
        <Route element={<AppShell theme={theme} onToggleTheme={onToggleTheme} />}>
          <Route index element={<WorkspacePage />} />
          <Route path="projects" element={<ProjectsPage />} />
          <Route path="library" element={<LibraryPage />} />
          <Route path="canvas/*" element={<Navigate to={paths.projects} replace />} />
          <Route path="studio/:canvasId" element={<NodeCanvasRoute />} />
          <Route path="projects/drama/*" element={<Navigate to={paths.projects} replace />} />
          <Route path="settings" element={<SettingsPage theme={theme} onToggleTheme={onToggleTheme} managedByAppShell />} />
          <Route path="*" element={<Navigate to={paths.home} replace />} />
        </Route>
      </Routes>
    </>
  )
}
