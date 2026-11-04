import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { Suspense } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '../../routing'
import { POLL_INTERVAL_MS } from '../../hooks/usePolling'
import { WorkspaceApiError, workspaceApi, type ProjectSummary } from '../../api/workspace'

vi.mock('../../api/workspace', async () => {
  const actual = await vi.importActual<typeof import('../../api/workspace')>('../../api/workspace')
  return { ...actual, workspaceApi: { overview: vi.fn(), project: vi.fn() } }
})
vi.mock('../workspace/WorkspacePage', () => ({ default: () => <div>WORKSPACE_PAGE</div> }))
vi.mock('../nodeCanvas/NodeCanvasPage', () => ({
  default: ({ canvasId }: { canvasId: string }) => <div>NODE_CANVAS:{canvasId}</div>,
}))

const summary: ProjectSummary = {
  canvas_id: 'c1',
  project_id: 'p1',
  title: '雪夜狐妖',
  aspect_ratio: '9:16',
  total_cost: '38.20',
  shots_total: 12,
  shots_done: 6,
  steps: [
    { key: 'script', status: 'done', label: '剧本' },
    { key: 'characters', status: 'done', label: '角色' },
    { key: 'storyboard', status: 'active', label: '分镜 6/12' },
    { key: 'voice', status: 'todo', label: '配音' },
    { key: 'final', status: 'todo', label: '成片' },
  ],
  active_step: 'storyboard',
  next_hint: '为 2 个镜头选图',
  running_count: 3,
  budget: { cap: '50.00', spent_today: '12.40', in_flight: '0', remaining: '37.60' },
}

function Probe() {
  const location = useLocation()
  return <output aria-label="location">{location.pathname}</output>
}

function GoTo({ to }: { to: string }) {
  const navigate = useNavigate()
  return <button onClick={() => navigate(to)}>GO_TEST</button>
}

function renderAt(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Suspense fallback={<div>LOADING</div>}>
        <AppRoutes theme="light" onToggleTheme={() => undefined} />
        <Probe />
        <GoTo to="/studio/c1/script" />
      </Suspense>
    </MemoryRouter>,
  )
}

describe('ProjectFrame', () => {
  beforeEach(() => {
    vi.mocked(workspaceApi.project).mockReset()
    vi.mocked(workspaceApi.project).mockResolvedValue(summary)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('redirects /studio/:id to the canvas view without step nav or footer', async () => {
    renderAt('/studio/c1')
    expect(await screen.findByText('NODE_CANVAS:c1')).toBeInTheDocument()
    expect(screen.getByLabelText('location')).toHaveTextContent('/studio/c1/canvas')
    expect(screen.queryByRole('navigation', { name: '制作步骤' })).not.toBeInTheDocument()
    expect(screen.queryByRole('contentinfo')).not.toBeInTheDocument()
    expect(await screen.findByText('雪夜狐妖')).toBeInTheDocument()
  })

  it('shows header pills: running count, budget and disabled AI button', async () => {
    renderAt('/studio/c1/storyboard')
    expect(await screen.findByText('3 个任务生成中')).toBeInTheDocument()
    expect(screen.getByText(/今日 ¥12.40 \/ ¥50/)).toBeInTheDocument()
    expect(screen.getByText('第 1 集')).toBeInTheDocument()
    const ai = screen.getByRole('button', { name: 'AI 助手' })
    expect(ai).toBeDisabled()
    expect(ai).toHaveAttribute('title', 'AI 助手即将上线')
  })

  it('shows idle text and spend-only budget when nothing runs and there is no cap', async () => {
    vi.mocked(workspaceApi.project).mockResolvedValue({
      ...summary,
      running_count: 0,
      budget: { cap: null, spent_today: '1.00', in_flight: '0', remaining: null },
    })
    renderAt('/studio/c1/script')
    expect(await screen.findByText('没有进行中的任务')).toBeInTheDocument()
    expect(screen.getByText('今日 ¥1.00')).toBeInTheDocument()
  })

  it('renders the step nav with done / active / todo and aria-current on the current page', async () => {
    renderAt('/studio/c1/storyboard')
    const nav = await screen.findByRole('navigation', { name: '制作步骤' })
    await within(nav).findByText('✓ 剧本')
    const links = within(nav).getAllByRole('link')
    expect(links.map((a) => a.textContent)).toEqual(['✓ 剧本', '✓ 角色', '3 分镜 6/12', '4 配音', '5 成片'])
    expect(links[0]).toHaveClass('pf-step--done')
    expect(links[2]).toHaveClass('pf-step--active')
    expect(links[3]).toHaveClass('pf-step--todo')
    expect(links.map((a) => a.getAttribute('aria-current'))).toEqual([null, null, 'step', null, null])
    expect(within(nav).getByText('本集成本 ¥38.20 · 12 镜头')).toBeInTheDocument()
  })

  it('footer shows hint and per-step next button that navigates', async () => {
    renderAt('/studio/c1/script')
    expect(await screen.findByText('为 2 个镜头选图')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: '确认剧本，去定角色 →' }))
    expect(await screen.findByRole('heading', { name: '角色' })).toBeInTheDocument()
    expect(screen.getByLabelText('location')).toHaveTextContent('/studio/c1/characters')
    expect(screen.getByRole('link', { name: '角色定稿，去分镜 →' })).toBeInTheDocument()
  })

  it.each([
    ['storyboard', '完成分镜，去配音 →', '/studio/c1/voice'],
    ['voice', '配音完成，去成片 →', '/studio/c1/final'],
  ])('%s next button leads to the following step', async (step, label, target) => {
    renderAt(`/studio/c1/${step}`)
    fireEvent.click(await screen.findByRole('link', { name: label }))
    await waitFor(() => expect(screen.getByLabelText('location')).toHaveTextContent(target))
  })

  it('final step has no next button, and the hint hides when empty', async () => {
    vi.mocked(workspaceApi.project).mockResolvedValue({ ...summary, next_hint: '' })
    renderAt('/studio/c1/final')
    await screen.findByText('3 个任务生成中')
    expect(screen.queryByRole('link', { name: /→/ })).not.toBeInTheDocument()
    expect(screen.queryByText('AI')).not.toBeInTheDocument()
  })

  it('placeholder step offers to continue in the canvas', async () => {
    renderAt('/studio/c1/voice')
    fireEvent.click(await screen.findByRole('link', { name: '在画布中继续' }))
    expect(await screen.findByText('NODE_CANVAS:c1')).toBeInTheDocument()
    expect(screen.getByLabelText('location')).toHaveTextContent('/studio/c1/canvas')
  })

  it('view switch: steps goes to the active step, canvas goes to the canvas', async () => {
    renderAt('/studio/c1/canvas')
    const group = await screen.findByRole('group', { name: '视图切换' })
    expect(within(group).getByRole('link', { name: '画布' })).toHaveAttribute('aria-current', 'page')
    await screen.findByText('3 个任务生成中')
    fireEvent.click(within(group).getByRole('link', { name: '步骤' }))
    expect(await screen.findByRole('heading', { name: '分镜' })).toBeInTheDocument()
    expect(screen.getByLabelText('location')).toHaveTextContent('/studio/c1/storyboard')
    fireEvent.click(within(screen.getByRole('group', { name: '视图切换' })).getByRole('link', { name: '画布' }))
    expect(await screen.findByText('NODE_CANVAS:c1')).toBeInTheDocument()
  })

  it('shows a not-found state on 404', async () => {
    vi.mocked(workspaceApi.project).mockRejectedValue(new WorkspaceApiError('项目不存在或无权访问', 404))
    renderAt('/studio/nope/canvas')
    expect(await screen.findByText('项目不存在或无权访问')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回工作台' })).toHaveAttribute('href', '/')
    expect(screen.queryByText('NODE_CANVAS:nope')).not.toBeInTheDocument()
  })

  it('clears the not-found state when switching from a missing project to a valid one', async () => {
    vi.mocked(workspaceApi.project).mockImplementation((id: string) =>
      id === 'nope'
        ? Promise.reject(new WorkspaceApiError('项目不存在或无权访问', 404))
        : new Promise<ProjectSummary>(() => undefined), // 新项目的请求一直在途
    )
    renderAt('/studio/nope/canvas')
    expect(await screen.findByText('项目不存在或无权访问')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'GO_TEST' }))
    await waitFor(() => expect(screen.queryByText('项目不存在或无权访问')).not.toBeInTheDocument())
    expect(screen.getByLabelText('项目加载中')).toBeInTheDocument()
  })

  it('polls every 10s and keeps only the latest response', async () => {
    vi.useFakeTimers()
    let resolveSlow: (value: ProjectSummary) => void = () => undefined
    vi.mocked(workspaceApi.project)
      .mockResolvedValueOnce(summary) // 首次
      .mockImplementationOnce(() => new Promise<ProjectSummary>((resolve) => { resolveSlow = resolve })) // 第 1 次轮询：慢
      .mockResolvedValueOnce({ ...summary, running_count: 1 }) // 第 2 次轮询：快
    renderAt('/studio/c1/script')
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByText('3 个任务生成中')).toBeInTheDocument()

    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS) })
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS) })
    expect(screen.getByText('1 个任务生成中')).toBeInTheDocument()

    // 慢的旧响应后到，不能覆盖新数据
    await act(async () => { resolveSlow({ ...summary, running_count: 9 }) })
    expect(screen.getByText('1 个任务生成中')).toBeInTheDocument()
    expect(workspaceApi.project).toHaveBeenCalledTimes(3)
  })
})
