import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import NewDramaDialog from './NewDramaDialog'
import WorkspacePage from './WorkspacePage'
import { POLL_INTERVAL_MS } from './useWorkspaceOverview'
import { nodeCanvasApi } from '../../api/nodeCanvas'
import { workspaceApi, type WorkspaceOverview } from '../../api/workspace'

vi.mock('../../api/workspace', () => ({ workspaceApi: { overview: vi.fn() } }))
vi.mock('../../api/nodeCanvas', () => ({ nodeCanvasApi: { create: vi.fn() } }))
vi.mock('../common/ProtectedMedia', () => ({
  ProtectedImage: ({ src }: { src: string }) => <img data-testid="cover" src={src} alt="" />,
}))

const overview: WorkspaceOverview = {
  budget: { cap: '50.00', spent_today: '12.40', in_flight: '0.31', remaining: '37.29' },
  counts: { running: 3, needs_review: 5, finished_this_week: 2, failed: 1 },
  projects: [
    {
      canvas_id: 'c1',
      project_id: 'p1',
      title: '雪夜狐妖',
      aspect_ratio: '9:16',
      updated_at: new Date(Date.now() - 2 * 60_000).toISOString(),
      cover_artifact_id: 'art-1',
      total_cost: '38.20',
      shots_total: 12,
      shots_done: 6,
      steps: [
        { key: 'script', status: 'done', label: '剧本' },
        { key: 'characters', status: 'todo', label: '角色' },
        { key: 'storyboard', status: 'active', label: '分镜 6/12' },
        { key: 'voice', status: 'todo', label: '配音' },
        { key: 'final', status: 'todo', label: '成片' },
      ],
      next_hint: '为 2 个镜头选图',
    },
  ],
  running: [
    { job_id: 'j1', canvas_id: 'c1', project_title: '雪夜狐妖', node_label: '镜头 4', kind: 'video', status: 'running', progress: 60, created_at: null },
    { job_id: 'j2', canvas_id: 'c1', project_title: '雪夜狐妖', node_label: '镜头 6', kind: 'image', status: 'queued', progress: 0, created_at: null },
  ],
  needs_review: [{ canvas_id: 'c1', project_title: '雪夜狐妖', node_id: 'n1', node_label: '镜头 3', reason: 'new_result' }],
  failed: [{ job_id: 'j3', canvas_id: 'c1', project_title: '雪夜狐妖', node_label: '镜头 5', error: '供应商超时' }],
}

function Studio() {
  return <div>OPEN_STUDIO:{useParams().canvasId}</div>
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/" element={<WorkspacePage />} />
        <Route path="/projects" element={<div>PROJECTS</div>} />
        <Route path="/studio/:canvasId" element={<Studio />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('WorkspacePage', () => {
  beforeEach(() => {
    vi.mocked(workspaceApi.overview).mockReset()
    vi.mocked(nodeCanvasApi.create).mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders subtitle, budget, count cards and project progress', async () => {
    vi.mocked(workspaceApi.overview).mockResolvedValue(overview)
    renderPage()
    expect(await screen.findByText('3 个任务正在生成，5 项等你确认')).toBeInTheDocument()
    expect(screen.getByText('¥12.40')).toBeInTheDocument()
    expect(screen.getByText(/\/ ¥50/)).toBeInTheDocument()

    const stats = within(screen.getByRole('region', { name: '概览' }))
    for (const [label, value] of [['生成中', '3'], ['等你确认', '5'], ['本周成片', '2'], ['失败待处理', '1']]) {
      expect(stats.getByText(label).parentElement).toHaveTextContent(value)
    }

    const project = screen.getByRole('link', { name: /雪夜狐妖 2 分钟前/ })
    expect(project).toHaveAttribute('href', '/studio/c1')
    expect(within(project).getByText('分镜 6/12 · 下一步：为 2 个镜头选图')).toBeInTheDocument()
    expect(within(project).getByText('累计 ¥38.20')).toBeInTheDocument()
    expect(within(project).getByRole('img', { name: /剧本已完成.*分镜进行中/ })).toBeInTheDocument()
    const segments = project.querySelectorAll('.ws-steps__seg > span')
    expect(Array.from(segments).map((el) => (el as HTMLElement).style.width)).toEqual(['100%', '0%', '50%', '0%', '0%'])
    expect(within(project).getByTestId('cover')).toHaveAttribute('src', '/api/artifacts/art-1/download')
  })

  it('shows running, failed and review panels linking into the project', async () => {
    vi.mocked(workspaceApi.overview).mockResolvedValue(overview)
    renderPage()
    const running = within(await screen.findByRole('region', { name: '正在生成' }))
    expect(running.getByText('生成中 60%')).toBeInTheDocument()
    expect(running.getByText('排队中')).toBeInTheDocument()
    expect(running.getByRole('link', { name: '查看' })).toHaveAttribute('href', '/studio/c1')
    const review = within(screen.getByRole('region', { name: '等你确认' }))
    expect(review.getByText(/镜头 3 有新结果/)).toBeInTheDocument()
    expect(review.getByRole('link')).toHaveAttribute('href', '/studio/c1')
  })

  it('shows only spend when there is no daily cap', async () => {
    vi.mocked(workspaceApi.overview).mockResolvedValue({ ...overview, budget: { cap: null, spent_today: '3.00', in_flight: '0.00', remaining: null } })
    renderPage()
    expect(await screen.findByText('¥3.00')).toBeInTheDocument()
    expect(screen.queryByText(/\/ ¥/)).not.toBeInTheDocument()
  })

  it('shows the first-drama guide when there are no projects', async () => {
    vi.mocked(workspaceApi.overview).mockResolvedValue({
      ...overview,
      counts: { running: 0, needs_review: 0, finished_this_week: 0, failed: 0 },
      projects: [],
      running: [],
      needs_review: [],
      failed: [],
    })
    renderPage()
    expect(await screen.findByText('开始你的第一部漫剧')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '概览' })).not.toBeInTheDocument()
  })

  it('shows a skeleton while loading', () => {
    vi.mocked(workspaceApi.overview).mockReturnValue(new Promise(() => undefined))
    renderPage()
    expect(screen.getByLabelText('工作台加载中')).toBeInTheDocument()
  })

  it('shows the error with a retry that reloads', async () => {
    vi.mocked(workspaceApi.overview).mockRejectedValueOnce(new Error('服务暂时不可用')).mockResolvedValue(overview)
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('服务暂时不可用')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('3 个任务正在生成，5 项等你确认')).toBeInTheDocument()
    expect(workspaceApi.overview).toHaveBeenCalledTimes(2)
  })

  it('polls every 10 seconds and pauses while the page is hidden', async () => {
    vi.useFakeTimers()
    vi.mocked(workspaceApi.overview).mockResolvedValue(overview)
    renderPage()
    await act(async () => {})
    expect(workspaceApi.overview).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS) })
    expect(workspaceApi.overview).toHaveBeenCalledTimes(2)

    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3) })
    expect(workspaceApi.overview).toHaveBeenCalledTimes(2)

    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    document.dispatchEvent(new Event('visibilitychange'))
    await act(async () => {})
    expect(workspaceApi.overview).toHaveBeenCalledTimes(3)
  })

  it('ignores an older response that arrives after a newer one', async () => {
    const older = { ...overview, counts: { ...overview.counts, running: 1 } }
    let resolveOld: (value: WorkspaceOverview) => void = () => undefined
    vi.mocked(workspaceApi.overview)
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
      .mockResolvedValue(overview)
    vi.useFakeTimers()
    renderPage()
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS) })
    expect(screen.getByText('3 个任务正在生成，5 项等你确认')).toBeInTheDocument()
    await act(async () => { resolveOld(older) })
    expect(screen.getByText('3 个任务正在生成，5 项等你确认')).toBeInTheDocument()
    expect(screen.queryByText('1 个任务正在生成，5 项等你确认')).not.toBeInTheDocument()
  })

  it('creates a drama from the dialog and enters the project', async () => {
    vi.mocked(workspaceApi.overview).mockResolvedValue(overview)
    vi.mocked(nodeCanvasApi.create).mockResolvedValue({ id: 'new-1' } as never)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /新建漫剧/ }))
    const dialog = within(screen.getByRole('dialog', { name: '新建漫剧' }))
    fireEvent.change(dialog.getByLabelText('故事想法'), { target: { value: '雪夜狐妖' } })
    fireEvent.click(dialog.getByLabelText('横屏 16:9'))
    fireEvent.click(dialog.getByRole('button', { name: '创建' }))
    expect(await screen.findByText('OPEN_STUDIO:new-1')).toBeInTheDocument()
    expect(nodeCanvasApi.create).toHaveBeenCalledWith({ title: '', aspect_ratio: '16:9', idea: '雪夜狐妖' })
  })

  it('keeps the dialog open and shows the error when creation fails', async () => {
    vi.mocked(workspaceApi.overview).mockResolvedValue(overview)
    vi.mocked(nodeCanvasApi.create).mockRejectedValue(new Error('画幅不支持'))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /新建漫剧/ }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '创建' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('画幅不支持'))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('NewDramaDialog', () => {
  it('focuses the textarea once and keeps focus when onClose changes', () => {
    const view = render(<MemoryRouter><NewDramaDialog onClose={() => undefined} /></MemoryRouter>)
    const textarea = screen.getByLabelText('故事想法')
    expect(textarea).toHaveFocus()
    screen.getByRole('button', { name: '取消' }).focus()
    view.rerender(<MemoryRouter><NewDramaDialog onClose={() => undefined} /></MemoryRouter>)
    expect(screen.getByRole('button', { name: '取消' })).toHaveFocus()
  })

  it('closes on Escape using the latest onClose', () => {
    const first = vi.fn()
    const second = vi.fn()
    const view = render(<MemoryRouter><NewDramaDialog onClose={first} /></MemoryRouter>)
    view.rerender(<MemoryRouter><NewDramaDialog onClose={second} /></MemoryRouter>)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalled()
  })
})
