import { lazy, Suspense } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useOutletContext, useParams } from 'react-router-dom'
import ProjectFrame from './components/project/ProjectFrame'
import type { ProjectOutletContext } from './components/project/outletContext'
import StepPlaceholder from './components/project/StepPlaceholder'
import { STEP_KEYS } from './components/project/steps'
import AppShell from './components/shell/AppShell'
import LibraryPage from './components/workspace/LibraryPage'
import ProjectsPage from './components/projects/ProjectsPage'
import SettingsPage from './components/SettingsPage'
import WorkspacePage from './components/workspace/WorkspacePage'
import { paths } from './routePaths'

const NodeCanvasPage = lazy(() => import('./components/nodeCanvas/NodeCanvasPage'))
const StoryboardPage = lazy(() => import('./components/storyboard/StoryboardPage'))

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

/** `/studio/:id` 进入后端判定的当前步骤；摘要加载中时框架（顶栏 / 步骤条骨架）先显示，失败时由框架的横幅重试。 */
function ActiveStepRedirect() {
  const { canvasId = '' } = useParams()
  const context = useOutletContext<ProjectOutletContext | null>()
  const summary = context?.summary
  if (summary) return <Navigate to={paths.projectStep(canvasId, summary.active_step ?? 'script')} replace />
  return (
    <div style={{ padding: 24, color: 'var(--text-secondary, #6b7280)' }}>
      {context?.loading === false ? <Link to={paths.projectCanvas(canvasId)}>项目进度暂时无法加载，先进入画布</Link> : '加载中…'}
    </div>
  )
}

function CanvasRedirect() {
  const { canvasId = '' } = useParams()
  return <Navigate to={paths.projectCanvas(canvasId)} replace />
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
          <Route path="studio/:canvasId" element={<ProjectFrame />}>
            <Route index element={<ActiveStepRedirect />} />
            <Route path="canvas" element={<NodeCanvasRoute />} />
            <Route path="storyboard" element={<Suspense fallback={pageFallback}><StoryboardPage /></Suspense>} />
            {STEP_KEYS.filter((key) => key !== 'storyboard').map((key) => (
              <Route key={key} path={key} element={<StepPlaceholder step={key} />} />
            ))}
            <Route path="*" element={<CanvasRedirect />} />
          </Route>
          <Route path="projects/drama/*" element={<Navigate to={paths.projects} replace />} />
          <Route path="settings" element={<SettingsPage theme={theme} onToggleTheme={onToggleTheme} managedByAppShell />} />
          <Route path="*" element={<Navigate to={paths.home} replace />} />
        </Route>
      </Routes>
    </>
  )
}
