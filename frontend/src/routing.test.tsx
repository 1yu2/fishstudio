import { Suspense } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AppRoutes, legacyRedirectFor } from './routing'

vi.mock('./components/workspace/WorkspacePage', () => ({
  default: () => <div>WORKSPACE_PAGE</div>,
}))
vi.mock('./components/workspace/LibraryPage', () => ({
  default: () => <div>LIBRARY_PAGE</div>,
}))
vi.mock('./components/projects/ProjectsPage', () => ({
  default: () => <div>PROJECTS_PAGE</div>,
}))
vi.mock('./components/SettingsPage', () => ({
  default: () => <div>SETTINGS_PAGE</div>,
}))
vi.mock('./components/nodeCanvas/NodeCanvasPage', () => ({
  default: ({ canvasId }: { canvasId: string }) => <div>NODE_CANVAS:{canvasId}</div>,
}))

function LocationProbe() {
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <div>
      <output aria-label="location">{location.pathname}{location.search}</output>
      <button onClick={() => navigate(-1)}>TEST_BACK</button>
      <button onClick={() => navigate(1)}>TEST_FORWARD</button>
    </div>
  )
}

function renderRoutes(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Suspense fallback={<div>TEST_LOADING</div>}>
        <AppRoutes theme="light" onToggleTheme={() => undefined} />
        <LocationProbe />
      </Suspense>
    </MemoryRouter>,
  )
}

describe('legacy URL mapping', () => {
  it.each([
    ['/?canvasId=canvas-1', '/projects'],
    ['/?page=studio', '/projects'],
    ['/?page=studio&projectId=drama-1', '/projects'],
    ['/?page=settings', '/settings'],
  ])('maps %s to %s', (input, expected) => {
    const url = new URL(input, 'https://fishstudio.local')
    expect(legacyRedirectFor(url.pathname, url.search)).toBe(expected)
  })

  it('does not rewrite new routes', () => {
    expect(legacyRedirectFor('/projects', '?page=settings')).toBeNull()
  })
})

describe('application navigation', () => {
  it('lists the four global navigation items', () => {
    renderRoutes('/')
    const nav = screen.getByRole('navigation', { name: '全局导航' })
    expect(Array.from(nav.querySelectorAll('a')).map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['工作台', '/'],
      ['我的项目', '/projects'],
      ['角色库', '/library'],
      ['设置', '/settings'],
    ])
    expect(screen.getByRole('link', { name: '工作台' })).toHaveClass('app-shell__nav-item--active')
  })

  it('navigates Workspace → Projects → Library → Settings from the global sidebar', async () => {
    renderRoutes('/')
    expect(screen.getByText('WORKSPACE_PAGE')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: '我的项目' }))
    expect(await screen.findByText('PROJECTS_PAGE')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: '角色库' }))
    expect(await screen.findByText('LIBRARY_PAGE')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: '设置' }))
    expect(await screen.findByText('SETTINGS_PAGE')).toBeInTheDocument()
  })

  it('opens and closes the mobile navigation drawer', () => {
    const { container } = renderRoutes('/')
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }))
    expect(container.querySelector('.app-shell__sidebar')).toHaveClass('app-shell__sidebar--open')
    fireEvent.click(screen.getByRole('button', { name: '关闭导航' }))
    expect(container.querySelector('.app-shell__sidebar')).not.toHaveClass('app-shell__sidebar--open')
  })

  it('keeps the project workspace full width', () => {
    const { container } = renderRoutes('/studio/node-1')
    expect(container.querySelector('.app-shell__content')).toHaveClass('app-shell__content--workspace')
  })

  it('supports browser back and forward navigation', async () => {
    renderRoutes('/')
    fireEvent.click(screen.getByRole('link', { name: '我的项目' }))
    fireEvent.click(screen.getByRole('link', { name: '设置' }))
    expect(screen.getByText('SETTINGS_PAGE')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'TEST_BACK' }))
    expect(await screen.findByText('PROJECTS_PAGE')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'TEST_FORWARD' }))
    expect(await screen.findByText('SETTINGS_PAGE')).toBeInTheDocument()
  })

  it.each([
    ['/studio/node-refresh', 'NODE_CANVAS:node-refresh'],
    ['/library', 'LIBRARY_PAGE'],
    ['/settings', 'SETTINGS_PAGE'],
  ])('restores a refreshed deep link %s', async (entry, expected) => {
    renderRoutes(entry)
    expect(await screen.findByText(expected)).toBeInTheDocument()
    expect(screen.getByLabelText('location')).toHaveTextContent(entry)
  })

  it.each([
    ['/?canvasId=legacy-canvas', 'PROJECTS_PAGE', '/projects'],
    ['/canvas/legacy-canvas', 'PROJECTS_PAGE', '/projects'],
    ['/?page=studio', 'PROJECTS_PAGE', '/projects'],
    ['/?page=studio&projectId=legacy-drama', 'PROJECTS_PAGE', '/projects'],
    ['/projects/drama/legacy-drama', 'PROJECTS_PAGE', '/projects'],
    ['/?page=settings', 'SETTINGS_PAGE', '/settings'],
    ['/prototype/projects', 'WORKSPACE_PAGE', '/'],
  ])('redirects legacy deep link %s with replace semantics', async (entry, content, expectedLocation) => {
    renderRoutes(entry)
    expect(await screen.findByText(content)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('location')).toHaveTextContent(expectedLocation))
  })
})
