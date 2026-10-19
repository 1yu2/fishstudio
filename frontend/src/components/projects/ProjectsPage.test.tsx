import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProjectsPage from './ProjectsPage'
import { apiFetch } from '../../api'
import { listProjects } from '../../api/series'
import { nodeCanvasApi } from '../../api/nodeCanvas'

vi.mock('../../api', () => ({
  apiFetch: vi.fn(),
}))
vi.mock('../../api/series', () => ({
  listProjects: vi.fn(),
}))
vi.mock('../../api/nodeCanvas', () => ({
  nodeCanvasApi: { list: vi.fn(), create: vi.fn() },
}))
vi.mock('../ui/Toast', () => ({
  toast: vi.fn(),
}))

function Destination({ type }: { type: 'canvas' | 'drama' | 'studio' }) {
  const params = useParams()
  if (type === 'studio') return <div>{`OPEN_STUDIO:${params.canvasId}`}</div>
  return <div>{type === 'canvas' ? `OPEN_CANVAS:${params.canvasId}` : `OPEN_DRAMA:${params.projectId}`}</div>
}

function renderProjects() {
  return render(
    <MemoryRouter initialEntries={['/projects']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/canvas/:canvasId" element={<Destination type="canvas" />} />
        <Route path="/studio/:canvasId" element={<Destination type="studio" />} />
        <Route path="/projects/drama/:projectId" element={<Destination type="drama" />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ProjectsPage', () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockResolvedValue(new Response(JSON.stringify([
      { id: 'canvas-1', name: '自由提案', createdAt: 1 },
    ]), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.mocked(listProjects).mockResolvedValue([
      { id: 'drama-1', title: '午夜漫剧', project_type: 'short_drama', status: 'draft' },
    ])
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

  it('aggregates and labels Freeform and Drama projects', async () => {
    renderProjects()
    expect(await screen.findByRole('link', { name: /自由提案/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /午夜漫剧/ })).toBeInTheDocument()
    expect(screen.getAllByText('Freeform / 自由画布').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Drama / 漫剧').length).toBeGreaterThan(0)
  })

  it('opens the existing Canvas route', async () => {
    renderProjects()
    fireEvent.click(await screen.findByRole('link', { name: /自由提案/ }))
    expect(await screen.findByText('OPEN_CANVAS:canvas-1')).toBeInTheDocument()
  })

  it('opens the existing Drama route', async () => {
    renderProjects()
    fireEvent.click(await screen.findByRole('link', { name: /午夜漫剧/ }))
    expect(await screen.findByText('OPEN_DRAMA:drama-1')).toBeInTheDocument()
  })
})
