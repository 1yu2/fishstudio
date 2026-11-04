import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProjectsPage from './ProjectsPage'
import { workspaceApi, type WorkspaceProject } from '../../api/workspace'

vi.mock('../../api/workspace', () => ({ workspaceApi: { overview: vi.fn() } }))
vi.mock('../../api/nodeCanvas', () => ({ nodeCanvasApi: { create: vi.fn() } }))
vi.mock('../common/ProtectedMedia', () => ({ ProtectedImage: () => <img alt="" /> }))

const project = (id: string, title: string): WorkspaceProject => ({
  canvas_id: id,
  project_id: `p-${id}`,
  title,
  aspect_ratio: '9:16',
  updated_at: null,
  cover_artifact_id: null,
  total_cost: '1.50',
  shots_total: 0,
  shots_done: 0,
  steps: [
    { key: 'script', status: 'active', label: '剧本' },
    { key: 'characters', status: 'todo', label: '角色' },
    { key: 'storyboard', status: 'todo', label: '分镜' },
    { key: 'voice', status: 'todo', label: '配音' },
    { key: 'final', status: 'todo', label: '成片' },
  ],
  next_hint: '添加第一个镜头',
})

function Studio() {
  return <div>OPEN_STUDIO:{useParams().canvasId}</div>
}

function renderProjects() {
  return render(
    <MemoryRouter initialEntries={['/projects']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/studio/:canvasId" element={<Studio />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ProjectsPage', () => {
  beforeEach(() => {
    vi.mocked(workspaceApi.overview).mockResolvedValue({
      budget: { cap: null, spent_today: '0.00', in_flight: '0.00', remaining: null },
      counts: { running: 0, needs_review: 0, finished_this_week: 0, failed: 0 },
      projects: [project('c1', '雨夜便利店'), project('c2', '雪夜狐妖')],
      running: [],
      needs_review: [],
      failed: [],
    })
  })

  it('lists projects as cards and opens one in the studio route', async () => {
    renderProjects()
    expect(await screen.findByRole('link', { name: /雪夜狐妖/ })).toBeInTheDocument()
    expect(screen.getAllByText('累计 ¥1.50')).toHaveLength(2)
    fireEvent.click(screen.getByRole('link', { name: /雨夜便利店/ }))
    expect(await screen.findByText('OPEN_STUDIO:c1')).toBeInTheDocument()
  })

  it('filters by search and no longer shows freeform canvases', async () => {
    renderProjects()
    await screen.findByRole('link', { name: /雪夜狐妖/ })
    fireEvent.change(screen.getByLabelText('搜索项目'), { target: { value: '雨夜' } })
    expect(screen.queryByRole('link', { name: /雪夜狐妖/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/自由画布/)).not.toBeInTheDocument()
  })

  it('shows an error with retry', async () => {
    vi.mocked(workspaceApi.overview).mockRejectedValueOnce(new Error('加载失败了'))
    renderProjects()
    expect(await screen.findByRole('alert')).toHaveTextContent('加载失败了')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByRole('link', { name: /雪夜狐妖/ })).toBeInTheDocument()
  })
})
