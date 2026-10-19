import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProjectsPage from './ProjectsPage'
import { apiFetch } from '../../api'
import { nodeCanvasApi } from '../../api/nodeCanvas'

vi.mock('../../api', () => ({
  apiFetch: vi.fn(),
}))
vi.mock('../../api/nodeCanvas', () => ({
  nodeCanvasApi: { list: vi.fn(), create: vi.fn() },
}))
vi.mock('../ui/Toast', () => ({
  toast: vi.fn(),
}))

function Destination({ type }: { type: 'canvas' | 'studio' }) {
  const params = useParams()
  return <div>{type === 'studio' ? `OPEN_STUDIO:${params.canvasId}` : `OPEN_CANVAS:${params.canvasId}`}</div>
}

function renderProjects() {
  return render(
    <MemoryRouter initialEntries={['/projects']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/canvas/:canvasId" element={<Destination type="canvas" />} />
        <Route path="/studio/:canvasId" element={<Destination type="studio" />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ProjectsPage', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockResolvedValue(new Response(JSON.stringify([
      { id: 'canvas-1', name: '自由提案', createdAt: 1 },
    ]), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.mocked(nodeCanvasApi.list).mockResolvedValue([
      { id: 'node-1', title: '雨夜便利店', aspect_ratio: '9:16', node_count: 3, updated_at: null, created_at: null },
    ])
  })

  it('lists node canvases and opens them in the studio route', async () => {
    renderProjects()
    fireEvent.click(await screen.findByRole('link', { name: /雨夜便利店/ }))
    expect(await screen.findByText('OPEN_STUDIO:node-1')).toBeInTheDocument()
  })

  it('creates a node canvas and enters it', async () => {
    vi.mocked(nodeCanvasApi.create).mockResolvedValue({
      id: 'node-2', title: '短片 2', aspect_ratio: '9:16', node_count: 0, updated_at: null, created_at: null, graph: {}, revision: 1,
    })
    renderProjects()
    fireEvent.click(await screen.findByRole('button', { name: /新建短片/ }))
    expect(await screen.findByText('OPEN_STUDIO:node-2')).toBeInTheDocument()
    expect(nodeCanvasApi.create).toHaveBeenCalledWith({ title: '短片 2' })
  })

  it('lists node canvases and old freeform canvases separately', async () => {
    renderProjects()
    expect(await screen.findByRole('link', { name: /自由提案/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /雨夜便利店/ })).toBeInTheDocument()
    expect(screen.queryByText(/漫剧/)).not.toBeInTheDocument()
  })

  it('opens the existing Canvas route', async () => {
    renderProjects()
    fireEvent.click(await screen.findByRole('link', { name: /自由提案/ }))
    expect(await screen.findByText('OPEN_CANVAS:canvas-1')).toBeInTheDocument()
  })

})
