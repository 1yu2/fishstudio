import { Suspense } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AppRoutes, legacyRedirectFor } from './routing'

vi.mock('./components/HomePage', () => ({
  default: () => <div>HOME_PAGE</div>,
}))
vi.mock('./components/projects/ProjectsPage', () => ({
  default: () => <div>PROJECTS_PAGE</div>,
}))
vi.mock('./components/SettingsPage', () => ({
  default: () => <div>SETTINGS_PAGE</div>,
}))
vi.mock('./components/ChatInterface', () => ({
  default: ({ initialCanvasId }: { initialCanvasId: string }) => <div>CANVAS_PAGE:{initialCanvasId}</div>,
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
        <AppRoutes theme="light" onToggleTheme={() => undefined} onSetTheme={() => undefined} />
        <LocationProbe />
      </Suspense>
    </MemoryRouter>,
  )
}

describe('legacy URL mapping', () => {
  it.each([
    ['/?canvasId=canvas-1', '/canvas/canvas-1'],
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
  it('navigates Home → Projects → Settings from the global sidebar', async () => {
    renderRoutes('/')
    expect(screen.getByText('HOME_PAGE')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: 'Projects' }))
    expect(await screen.findByText('PROJECTS_PAGE')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: 'Settings' }))
    expect(await screen.findByText('SETTINGS_PAGE')).toBeInTheDocument()
  })

  it('opens and closes the mobile navigation drawer', () => {
    const { container } = renderRoutes('/')
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }))
    expect(container.querySelector('.app-shell__sidebar')).toHaveClass('app-shell__sidebar--open')
    fireEvent.click(screen.getByRole('button', { name: '关闭导航' }))
    expect(container.querySelector('.app-shell__sidebar')).not.toHaveClass('app-shell__sidebar--open')
  })

  it('supports browser back and forward navigation', async () => {
    renderRoutes('/')
    fireEvent.click(screen.getByRole('link', { name: 'Projects' }))
    fireEvent.click(screen.getByRole('link', { name: 'Settings' }))
    expect(screen.getByText('SETTINGS_PAGE')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'TEST_BACK' }))
    expect(await screen.findByText('PROJECTS_PAGE')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'TEST_FORWARD' }))
    expect(await screen.findByText('SETTINGS_PAGE')).toBeInTheDocument()
  })

  it.each([
    ['/canvas/canvas-refresh', 'CANVAS_PAGE:canvas-refresh'],
    ['/studio/node-refresh', 'NODE_CANVAS:node-refresh'],
    ['/settings', 'SETTINGS_PAGE'],
  ])('restores a refreshed deep link %s', async (entry, expected) => {
    renderRoutes(entry)
    expect(await screen.findByText(expected)).toBeInTheDocument()
    expect(screen.getByLabelText('location')).toHaveTextContent(entry)
  })

  it.each([
    ['/?canvasId=legacy-canvas', 'CANVAS_PAGE:legacy-canvas', '/canvas/legacy-canvas'],
    ['/?page=studio', 'PROJECTS_PAGE', '/projects'],
    ['/?page=studio&projectId=legacy-drama', 'PROJECTS_PAGE', '/projects'],
    ['/projects/drama/legacy-drama', 'PROJECTS_PAGE', '/projects'],
    ['/?page=settings', 'SETTINGS_PAGE', '/settings'],
  ])('redirects legacy deep link %s with replace semantics', async (entry, content, expectedLocation) => {
    renderRoutes(entry)
    expect(await screen.findByText(content)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('location')).toHaveTextContent(expectedLocation))
  })
})
