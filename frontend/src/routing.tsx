import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import AppShell from './components/shell/AppShell'
import HomePage from './components/HomePage'
import ProjectsPage from './components/projects/ProjectsPage'
import SettingsPage from './components/SettingsPage'
import {
  PrototypeCharacters,
  PrototypeDramaImport,
  PrototypeDramaParse,
  PrototypeFreeWorkspace,
  PrototypeHome,
  PrototypeNewProject,
  PrototypePlaceholder,
  PrototypeProjects,
  PrototypeShell,
  PrototypeStoryboard,
} from './components/prototype/PrototypeStudio'
import { paths } from './routePaths'

const ChatInterface = lazy(() => import('./components/ChatInterface'))
const StudioHome = lazy(() => import('./components/studio/StudioHome'))
const StudioPage = lazy(() => import('./components/studio/StudioPage'))

export function legacyRedirectFor(pathname: string, search: string): string | null {
  if (pathname !== '/') return null

  const params = new URLSearchParams(search)
  const page = params.get('page')
  if (page === 'settings') return paths.settings
  if (page === 'studio') {
    const projectId = params.get('projectId')
    return projectId ? paths.dramaProject(projectId) : paths.dramaHome
  }

  const canvasId = params.get('canvasId')
  return canvasId ? paths.canvas(canvasId) : null
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
  onSetTheme: (theme: ThemeMode) => void
}

const pageFallback = (
  <div style={{ padding: 24, color: 'var(--text-secondary, #6b7280)' }}>加载中…</div>
)

function CanvasRoute(props: AppRoutesProps) {
  const { canvasId = '' } = useParams()
  return (
    <Suspense fallback={pageFallback}>
      <ChatInterface
        initialCanvasId={canvasId}
        theme={props.theme}
        onToggleTheme={props.onToggleTheme}
        onSetTheme={props.onSetTheme}
        managedByAppShell
      />
    </Suspense>
  )
}

function DramaProjectRoute(props: AppRoutesProps) {
  const { projectId = '' } = useParams()
  return (
    <Suspense fallback={pageFallback}>
      <StudioPage
        projectId={projectId}
        theme={props.theme}
        onToggleTheme={props.onToggleTheme}
        managedByAppShell
      />
    </Suspense>
  )
}

function DramaHomeRoute(props: AppRoutesProps) {
  return (
    <Suspense fallback={pageFallback}>
      <StudioHome theme={props.theme} onToggleTheme={props.onToggleTheme} managedByAppShell />
    </Suspense>
  )
}

export function AppRoutes({ theme, onToggleTheme, onSetTheme }: AppRoutesProps) {
  return (
    <>
      <LegacyUrlRedirect />
      <Routes>
        <Route path="prototype" element={<PrototypeShell />}>
          <Route index element={<PrototypeHome />} />
          <Route path="projects" element={<PrototypeProjects />} />
          <Route path="projects/new" element={<PrototypeNewProject />} />
          <Route path="projects/new/drama-import" element={<PrototypeDramaImport />} />
          <Route path="projects/new/drama-parse" element={<PrototypeDramaParse />} />
          <Route path="projects/midnight-convenience/characters" element={<PrototypeCharacters />} />
          <Route path="projects/product-poster/workspace" element={<PrototypeFreeWorkspace />} />
          <Route path="projects/midnight-convenience/storyboard" element={<PrototypeStoryboard />} />
          <Route path="assets" element={<PrototypePlaceholder />} />
          <Route path="jobs" element={<PrototypePlaceholder />} />
          <Route path="settings" element={<PrototypePlaceholder />} />
        </Route>
        <Route element={<AppShell theme={theme} onToggleTheme={onToggleTheme} />}>
          <Route index element={<HomePage theme={theme} onToggleTheme={onToggleTheme} managedByAppShell />} />
          <Route path="projects" element={<ProjectsPage />} />
          <Route path="canvas/:canvasId" element={<CanvasRoute theme={theme} onToggleTheme={onToggleTheme} onSetTheme={onSetTheme} />} />
          <Route path="projects/drama" element={<DramaHomeRoute theme={theme} onToggleTheme={onToggleTheme} onSetTheme={onSetTheme} />} />
          <Route path="projects/drama/:projectId" element={<DramaProjectRoute theme={theme} onToggleTheme={onToggleTheme} onSetTheme={onSetTheme} />} />
          <Route path="settings" element={<SettingsPage theme={theme} onToggleTheme={onToggleTheme} managedByAppShell />} />
          <Route path="*" element={<Navigate to={paths.home} replace />} />
        </Route>
      </Routes>
    </>
  )
}
