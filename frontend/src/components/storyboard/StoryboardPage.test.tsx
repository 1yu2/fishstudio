import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConflictError, nodeCanvasApi, type BatchQuote, type CanvasRuns, type StoredNode } from '../../api/nodeCanvas'
import { toast } from '../ui/Toast'
import StoryboardPage from './StoryboardPage'

vi.mock('../../api/nodeCanvas', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/nodeCanvas')>()
  return {
    ...actual,
    nodeCanvasApi: {
      get: vi.fn(), models: vi.fn(), runs: vi.fn(), save: vi.fn(), saveKeepalive: vi.fn(), quote: vi.fn(), run: vi.fn(), split: vi.fn(),
      batchQuote: vi.fn(), batchRun: vi.fn(), batchList: vi.fn(), batchGet: vi.fn(), batchCancel: vi.fn(),
    },
  }
})
vi.mock('../ui/Toast', () => ({ toast: vi.fn() }))
vi.mock('../common/ProtectedMedia', () => ({
  ProtectedImage: ({ src }: { src: string }) => <img src={src} alt="" />,
  ProtectedVideo: ({ src }: { src: string }) => <video src={src} />,
}))

const api = vi.mocked(nodeCanvasApi)
const node = (id: string, type: StoredNode['type'], data: Record<string, unknown>, y = 0): StoredNode => ({ id, type, position: { x: type === 'video' ? 840 : 420, y }, data })
const link = (source: string, target: string, targetHandle: string) => ({ id: `e-${source}-${target}`, source, target, sourceHandle: 'out', targetHandle })

const graphNodes = (): StoredNode[] => [
  node('char', 'image', { label: '角色 · 狐妖', prompt: '狐妖' }, -400),
  node('k1', 'image', { label: '1 · 开场 关键帧', prompt: '雪夜竹林', shot_size: '近景', selected: 'a2' }, 0),
  node('v1', 'video', { label: '1 · 开场', prompt: '回头', duration: 4, order: 0 }, 0),
  node('k2', 'image', { label: '2 · 对视 关键帧', prompt: '书生抬头' }, 380),
  node('v2', 'video', { label: '2 · 对视', prompt: '', order: 1 }, 380),
]
const graphEdges = () => [link('char', 'k1', 'reference'), link('k1', 'v1', 'first_frame'), link('k2', 'v2', 'first_frame')]

const result = (artifact_id: string, extra: Record<string, unknown> = {}) => ({ artifact_id, type: 'image', url: `/api/artifacts/${artifact_id}/download`, ...extra })
const runsValue = (): CanvasRuns => ({
  nodes: {
    // 最新在前：a3 基于 a2，a2 基于 a1，a1 是原始图
    char: { jobs: [], results: [result('c1')] },
    k1: { jobs: [], results: [result('a3', { edit_of: 'a2' }), result('a2', { edit_of: 'a1' }), result('a1')] },
  },
  active_jobs: 0,
  spent: 0,
})

const quoteOf = (kind: 'image' | 'video', total: number, runnable: number): BatchQuote => ({
  revision: 3, nodes: [], estimated_total: total, billable_total: total, runnable_count: runnable, waiting_count: 0, blocking_count: 0,
  unavailable_nodes: [], budget: { cap: null, remaining: null }, exceeds_budget: false,
  ...(kind === 'image' ? {} : {}),
})

function setupApi(overrides: { nodes?: StoredNode[]; edges?: ReturnType<typeof graphEdges>; runs?: CanvasRuns } = {}) {
  api.get.mockResolvedValue({
    id: 'c1', title: '雪夜狐妖', aspect_ratio: '9:16', node_count: 5, updated_at: null, created_at: null, revision: 3,
    graph: { nodes: overrides.nodes ?? graphNodes(), edges: overrides.edges ?? graphEdges() },
  })
  api.models.mockResolvedValue({ image: [], video: [], defaults: { image: 'seedream', video_preview: 'h', video_final: 'h' } })
  api.runs.mockResolvedValue(overrides.runs ?? runsValue())
  api.save.mockResolvedValue({ revision: 4 })
  api.quote.mockResolvedValue({ kind: 'image', model: 'seedream', estimated_cost: 0.12 })
  api.run.mockResolvedValue({ job_id: 'j1', status: 'pending' })
  api.batchList.mockResolvedValue([])
  api.batchQuote.mockImplementation(async (_id, body) => body.kinds?.[0] === 'video' ? quoteOf('video', 1.02, 2) : quoteOf('image', 0.72, 6))
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/studio/c1/storyboard']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes><Route path="/studio/:canvasId/storyboard" element={<StoryboardPage />} /></Routes>
    </MemoryRouter>,
  )
}

const lastSave = () => api.save.mock.calls[api.save.mock.calls.length - 1]

/** 最近一次保存的 graph 里某节点的 data */
function savedData(nodeId: string) {
  const graph = lastSave()[1]
  return graph.nodes!.find((item) => item.id === nodeId)!.data
}

beforeEach(() => {
  vi.clearAllMocks()
  setupApi()
})

describe('StoryboardPage strip', () => {
  it('lists shots in order with caption, total seconds and estimate buttons', async () => {
    renderPage()
    expect(await screen.findByText('2 个镜头 · 约 9 秒 · 拖动可调整顺序')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '镜头 1：近景' })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: '镜头 2：未生成' })).toBeInTheDocument()
    // 角色图不算镜头
    expect(screen.queryByRole('button', { name: /镜头 3/ })).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '生成剩余图片 · 预估 ¥0.720' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '全部转视频 · 预估 ¥1.02' })).toBeEnabled()
  })

  it('disables a batch button and says why when nothing is left to run', async () => {
    api.batchQuote.mockImplementation(async () => quoteOf('image', 0, 0))
    renderPage()
    const button = await screen.findByRole('button', { name: '生成剩余图片' })
    await waitFor(() => expect(button).toBeDisabled())
    expect(button).toHaveAttribute('title', '所有镜头都已有画面')
    expect(screen.getByText('画面都已生成')).toBeInTheDocument()
  })

  it('says in text that video batches need a video model', async () => {
    api.models.mockResolvedValue({ image: [], video: [], defaults: { image: 's', video_preview: '', video_final: '' } })
    renderPage()
    expect(await screen.findByText('还没有设置视频模型')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '全部转视频' })).toBeDisabled()
  })

  it('opens the quote dialog for the clicked batch and submits with its kinds', async () => {
    api.batchRun.mockResolvedValue({ id: 'b1', status: 'running', kinds: ['video'], node_ids: [], nodes: {}, total: 2, completed: 0, failed: 0, estimated_total: 1.02 })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '全部转视频 · 预估 ¥1.02' }))
    expect(await screen.findByRole('complementary', { name: '批量运行' })).toBeInTheDocument()
    expect(api.batchQuote).toHaveBeenLastCalledWith('c1', { scope: 'all', kinds: ['video'] })
    fireEvent.click(await screen.findByRole('button', { name: '确认运行' }))
    await waitFor(() => expect(api.batchRun).toHaveBeenCalledTimes(1))
    expect(api.batchRun.mock.calls[0][1]).toMatchObject({ scope: 'all', kinds: ['video'], skip_succeeded: true, quoted_revision: 3 })
    expect(await screen.findByText(/视频批次 0\/2 完成/)).toBeInTheDocument()
  })

  it('shows the pick-then-convert hint after an image batch finishes', async () => {
    api.batchList.mockResolvedValue([{ id: 'b0', status: 'succeeded', kinds: ['image'], node_ids: [], nodes: {}, total: 2, completed: 2, failed: 0, estimated_total: 0.2 }])
    renderPage()
    expect(await screen.findByText(/画面已完成：挑选满意的图/)).toBeInTheDocument()
    expect(screen.getByText(/画面批次 2\/2 完成/)).toBeInTheDocument()
  })

  it('reorders by drag and drop: rewrites order for every shot and saves with the revision', async () => {
    renderPage()
    const first = await screen.findByRole('button', { name: '镜头 1：近景' })
    const second = screen.getByRole('button', { name: '镜头 2：未生成' })
    const dataTransfer = { setData: vi.fn() }
    fireEvent.dragStart(first, { dataTransfer })
    fireEvent.dragOver(second, { dataTransfer })
    fireEvent.drop(second, { dataTransfer })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    const [, graph, revision] = lastSave()
    expect(revision).toBe(3)
    expect(graph.nodes!.find((item) => item.id === 'v2')!.data.order).toBe(0)
    expect(graph.nodes!.find((item) => item.id === 'v1')!.data.order).toBe(1)
    // 顺序变了：原来的第二个镜头现在排第一，选中的仍是原来的镜头（现在排第二）
    expect(screen.getByRole('button', { name: '镜头 1：未生成' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: /镜头 2 · 开场/ })).toBeInTheDocument()
  })

  it('moves the selected shot with the keyboard-friendly buttons', async () => {
    renderPage()
    await screen.findByRole('heading', { level: 2, name: /镜头 1/ })
    expect(screen.getByRole('button', { name: '左移' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '右移' }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(savedData('v1').order).toBe(1)
    expect(savedData('v2').order).toBe(0)
    expect(await screen.findByRole('heading', { level: 2, name: /镜头 2 · 开场/ })).toBeInTheDocument()
  })

  it('adds a shot at the end: image + video linked by first_frame, selected', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '添加镜头' }))
    expect(await screen.findByRole('heading', { level: 2, name: /镜头 3/ })).toBeInTheDocument()
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    const graph = lastSave()[1]
    expect(graph.nodes).toHaveLength(7)
    const video = graph.nodes!.find((item) => item.type === 'video' && item.data.order === 2)!
    expect(graph.edges!.some((item) => item.target === video.id && item.targetHandle === 'first_frame')).toBe(true)
    expect(screen.getByText('3 个镜头 · 约 14 秒 · 拖动可调整顺序')).toBeInTheDocument()
  })
})

describe('StoryboardPage detail', () => {
  it('draws the edit chain and writes the clicked version back as the selection', async () => {
    renderPage()
    const chain = await screen.findByText('修改记录')
    expect(chain).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '第 1 张' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '改 1 选用' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('基于“改 1”继续修改')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '改 2' }))
    expect(screen.getByRole('button', { name: '改 2 选用' })).toBeInTheDocument()
    expect(screen.getByText('基于“改 2”继续修改')).toBeInTheDocument()
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(savedData('k1').selected).toBe('a3')
  })

  it('submits an edit based on the chosen version with a fresh client_request_id', async () => {
    renderPage()
    const box = await screen.findByLabelText('想改哪里')
    fireEvent.change(box, { target: { value: '嘴角不要笑' } })
    fireEvent.click(screen.getByRole('button', { name: '换光线' }))
    expect(box).toHaveValue('嘴角不要笑，换光线')
    const submit = await screen.findByRole('button', { name: '按要求修改 · ¥0.120' })
    fireEvent.click(submit)
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1))
    const [canvasId, nodeId, body] = api.run.mock.calls[0]
    expect([canvasId, nodeId]).toEqual(['c1', 'k1'])
    expect(body).toMatchObject({
      kind: 'image', prompt: '嘴角不要笑，换光线，保持构图和角色长相不变', edit_of: 'a2', edit_instruction: '嘴角不要笑，换光线', count: 2, aspect_ratio: '9:16',
    })
    expect(body.client_request_id).toBeTruthy()
    // 报价用同样的 edit_of 走图片编辑路由
    expect(api.quote.mock.calls.some(([, request]) => request.edit_of === 'a2' && request.count === 2)).toBe(true)
    await waitFor(() => expect(box).toHaveValue(''))
  })

  it('leaves out the keep-composition phrase when unchecked, and sends the chosen count', async () => {
    renderPage()
    fireEvent.change(await screen.findByLabelText('想改哪里'), { target: { value: '换动作' } })
    fireEvent.click(screen.getByLabelText('保持构图和角色长相'))
    fireEvent.change(screen.getByLabelText('修改张数'), { target: { value: '4' } })
    fireEvent.click(screen.getByRole('button', { name: /按要求修改/ }))
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1))
    expect(api.run.mock.calls[0][2]).toMatchObject({ prompt: '换动作', edit_instruction: '换动作', count: 4 })
  })

  it('explains an unavailable image model instead of letting the edit be submitted', async () => {
    api.models.mockResolvedValue({ image: [], video: [], defaults: { image: '', video_preview: '', video_final: '' }, image_edit_problem: '还没有可用的图片模型' })
    renderPage()
    fireEvent.change(await screen.findByLabelText('想改哪里'), { target: { value: '换表情' } })
    expect((await screen.findAllByText('还没有可用的图片模型')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /按要求修改/ })).toBeDisabled()
  })

  it('edits description, shot size, camera and duration back into the nodes', async () => {
    renderPage()
    fireEvent.change(await screen.findByLabelText('画面描述'), { target: { value: '新的画面' } })
    fireEvent.change(screen.getByLabelText('景别'), { target: { value: '特写' } })
    fireEvent.change(screen.getByLabelText('镜头运动'), { target: { value: '缓慢推近' } })
    fireEvent.change(screen.getByLabelText('时长'), { target: { value: '8' } })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(savedData('k1')).toMatchObject({ prompt: '新的画面', shot_size: '特写' })
    expect(savedData('v1')).toMatchObject({ camera: '缓慢推近', duration: 8 })
  })

  it('accepts a custom camera move', async () => {
    renderPage()
    fireEvent.change(await screen.findByLabelText('镜头运动'), { target: { value: '__custom' } })
    fireEvent.change(screen.getByLabelText('自定义运镜'), { target: { value: '从肩后绕到正面' } })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(savedData('v1').camera).toBe('从肩后绕到正面')
  })

  it('has an editable cast picker instead of the placeholder, and dialogue is editable', async () => {
    renderPage()
    await screen.findByLabelText('画面描述')
    expect(screen.queryByText('第 4 步上线后可编辑')).not.toBeInTheDocument()
    expect(screen.getByRole('group', { name: '出镜角色' })).toBeInTheDocument()
    expect(screen.queryByText('第 6 步上线后可编辑')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加台词' })).toBeInTheDocument()
  })

  it('quotes single nodes, runs them with the resolved request and explains blockers', async () => {
    renderPage()
    const regenerate = await screen.findByRole('button', { name: '重新生成图片 · ¥0.120' })
    // 视频的首帧有图（a2），可以生成
    fireEvent.click(await screen.findByRole('button', { name: /生成视频/ }))
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1))
    expect(api.run.mock.calls[0][1]).toBe('v1')
    expect(api.run.mock.calls[0][2]).toMatchObject({ kind: 'video', first_frame_artifact_id: 'a2', duration: 4 })
    fireEvent.click(regenerate)
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(2))
    expect(api.run.mock.calls[1][2]).toMatchObject({ kind: 'image', prompt: '雪夜竹林\n景别：近景', reference_artifact_ids: ['c1'] })
  })

  it('disables 生成视频 with the reason when the first frame has no image yet', async () => {
    renderPage()
    await screen.findByLabelText('画面描述')
    fireEvent.click(screen.getByRole('button', { name: '镜头 2：未生成' }))
    const video = await screen.findByRole('button', { name: '生成视频' })
    expect(video).toBeDisabled()
    expect(screen.getByText('首帧节点「2 · 对视 关键帧」还没有图片')).toBeInTheDocument()
  })

  it('disables the generate buttons and shows the reason when their quote fails', async () => {
    api.quote.mockRejectedValue(new Error('还没有设置视频模型'))
    renderPage()
    const video = await screen.findByRole('button', { name: /生成视频/ })
    await waitFor(() => expect(video).toBeDisabled())
    expect((await screen.findAllByText('还没有设置视频模型')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /重新生成图片/ })).toBeDisabled()
  })

  it('uses the edit-specific problem for the edit form, not the generation one', async () => {
    api.models.mockResolvedValue({ image: [], video: [], defaults: { image: 's', video_preview: 'h', video_final: 'h' }, image_problem: '', image_edit_problem: '没有图片编辑模型' })
    renderPage()
    fireEvent.change(await screen.findByLabelText('想改哪里'), { target: { value: '换表情' } })
    expect(await screen.findByText('没有图片编辑模型')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /按要求修改/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /重新生成图片/ })).toBeEnabled()
  })

  it('shows 未配置价格 on the edit button when the quote has no price', async () => {
    api.quote.mockResolvedValue({ kind: 'image', model: 'm', estimated_cost: null })
    renderPage()
    fireEvent.change(await screen.findByLabelText('想改哪里'), { target: { value: '换表情' } })
    expect(await screen.findByRole('button', { name: '按要求修改 · 未配置价格' })).toBeInTheDocument()
  })

  it('links to the canvas focused on the shot node', async () => {
    renderPage()
    const open = await screen.findByRole('link', { name: '在画布中打开' })
    expect(open).toHaveAttribute('href', '/studio/c1/canvas?node=v1')
  })

  it('refreshes after a revision conflict and says so', async () => {
    api.save.mockRejectedValueOnce(new ConflictError('conflict'))
    renderPage()
    fireEvent.change(await screen.findByLabelText('画面描述'), { target: { value: '改动' } })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('画布在别处被修改，已刷新', 'error'), { timeout: 3000 })
    expect(api.get).toHaveBeenCalledTimes(2)
  })
})

describe('StoryboardPage unsaved changes', () => {
  it('asks before leaving with unsaved edits and sends the latest graph on pagehide', async () => {
    renderPage()
    const quiet = new Event('beforeunload', { cancelable: true })
    await screen.findByLabelText('画面描述')
    window.dispatchEvent(quiet)
    expect(quiet.defaultPrevented).toBe(false)  // 没有改动：不打扰
    fireEvent.change(screen.getByLabelText('画面描述'), { target: { value: '改了一半' } })
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    window.dispatchEvent(new Event('pagehide'))
    expect(api.saveKeepalive).toHaveBeenCalledTimes(1)
    const [id, graph, revision] = api.saveKeepalive.mock.calls[0]
    expect([id, revision]).toEqual(['c1', 3])
    expect(graph.nodes!.find((item) => item.id === 'k1')!.data.prompt).toBe('改了一半')
  })

  it('includes an edit queued behind an in-flight save, using revision + 1', async () => {
    let finish: (value: { revision: number }) => void = () => undefined
    api.save.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    renderPage()
    fireEvent.change(await screen.findByLabelText('画面描述'), { target: { value: 'A' } })
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1), { timeout: 3000 })
    fireEvent.change(screen.getByLabelText('画面描述'), { target: { value: 'B' } })
    window.dispatchEvent(new Event('pagehide'))
    const [, graph, revision] = api.saveKeepalive.mock.calls[0]
    expect(revision).toBe(4)
    expect(graph.nodes!.find((item) => item.id === 'k1')!.data.prompt).toBe('B')
    await act(async () => { finish({ revision: 4 }) })
  })
})

describe('StoryboardPage polling and empty state', () => {
  it('keeps polling when an older runs response arrives late with no active jobs', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      let late: (value: CanvasRuns) => void = () => undefined
      api.runs
        .mockImplementationOnce(() => new Promise<CanvasRuns>((resolve) => { late = resolve }))
        .mockResolvedValue({ ...runsValue(), active_jobs: 1 })
      renderPage()
      await screen.findByText('2 个镜头 · 约 9 秒 · 拖动可调整顺序')
      await act(async () => { await vi.advanceTimersByTimeAsync(3100) })
      await act(async () => { late({ ...runsValue(), active_jobs: 0 }) })
      const before = api.runs.mock.calls.length
      await act(async () => { await vi.advanceTimersByTimeAsync(3100) })
      expect(api.runs.mock.calls.length).toBeGreaterThan(before)
    } finally { vi.useRealTimers() }
  })

  it('polls runs every 3 seconds while a job is active', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      api.runs.mockResolvedValue({ ...runsValue(), active_jobs: 1 })
      renderPage()
      await screen.findByText('2 个镜头 · 约 9 秒 · 拖动可调整顺序')
      const before = api.runs.mock.calls.length
      await act(async () => { await vi.advanceTimersByTimeAsync(3100) })
      expect(api.runs.mock.calls.length).toBeGreaterThan(before)
    } finally { vi.useRealTimers() }
  })

  it('splits an idea into shots from the empty state using the first text node', async () => {
    setupApi({ nodes: [node('t1', 'text', { text: '雪夜狐妖' })], edges: [], runs: { nodes: {}, active_jobs: 0, spent: 0 } })
    api.split.mockResolvedValue({
      characters: [{ name: '狐妖', description: '白发' }],
      shots: [{ title: '开场', prompt: '竹林', characters: ['狐妖'], duration_s: 4 }, { title: '回头', prompt: '回头', characters: [], duration_s: 3 }],
    })
    renderPage()
    expect(await screen.findByText('还没有镜头')).toBeInTheDocument()
    expect(screen.getByText(/雪夜狐妖/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '拆分镜头（会调用 AI）' }))
    await waitFor(() => expect(api.split).toHaveBeenCalledWith('c1', '雪夜狐妖', 6))
    expect(await screen.findByText(/2 个镜头/)).toBeInTheDocument()
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(lastSave()[1].nodes!.length).toBe(1 + 1 + 2 * 2)
  })

  it('asks for an idea when the canvas has no text node', async () => {
    setupApi({ nodes: [], edges: [], runs: { nodes: {}, active_jobs: 0, spent: 0 } })
    renderPage()
    const button = await screen.findByRole('button', { name: '拆分镜头（会调用 AI）' })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText('想法'), { target: { value: '一只猫' } })
    expect(button).toBeEnabled()
    expect(within(screen.getByLabelText('还没有镜头')).getByRole('link', { name: '到画布里自己搭' })).toHaveAttribute('href', '/studio/c1/canvas')
  })
})

describe('StoryboardPage dialogue lines', () => {
  const lineNodes = (): StoredNode[] => [
    node('a1', 'audio', { label: '台词 · 狐妖', speaker: '狐妖', text: '你来了', voice: '', order: 0 }, 0),
    node('a2', 'audio', { label: '台词 · 旁白', speaker: '旁白', text: '那一夜', voice: '', order: 1 }, 200),
    node('a3', 'audio', { label: '台词 · 书生', speaker: '书生', text: '第二个镜头的', order: 0 }, 380),
  ]
  const withLines = () => setupApi({
    nodes: [...graphNodes(), ...lineNodes()],
    edges: [...graphEdges(), link('a1', 'v1', 'voice'), link('a2', 'v1', 'voice'), link('a3', 'v2', 'voice')],
  })
  const savedGraph = () => lastSave()[1]

  it('lists only this shot\'s lines in order', async () => {
    withLines()
    renderPage()
    expect(await screen.findByDisplayValue('你来了')).toBeInTheDocument()
    expect(screen.getByDisplayValue('那一夜')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('第二个镜头的')).not.toBeInTheDocument()
    expect(screen.getByText('台词 · 2 句')).toBeInTheDocument()
    expect(screen.getByLabelText('第 1 句说话人')).toHaveValue('狐妖')
    expect(screen.getByLabelText('第 2 句说话人')).toHaveValue('旁白')
  })

  it('writes text and speaker edits back to the audio node', async () => {
    withLines()
    renderPage()
    fireEvent.change(await screen.findByLabelText('第 1 句台词'), { target: { value: '你终于来了' } })
    fireEvent.change(screen.getByLabelText('第 2 句说话人'), { target: { value: '狐妖' } })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(savedData('a1').text).toBe('你终于来了')
    expect(savedData('a2')).toMatchObject({ speaker: '狐妖', label: '台词 · 狐妖' })
  })

  it('adds a line: a new audio node with a voice edge to the shot video, ordered last', async () => {
    withLines()
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '添加台词' }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    const graph = savedGraph()
    const created = graph.nodes!.find((item) => item.type === 'audio' && !['a1', 'a2', 'a3'].includes(item.id))!
    expect(created.data).toMatchObject({ speaker: '狐妖', text: '', order: 2 })
    expect(graph.edges!.find((item) => item.source === created.id)).toMatchObject({ target: 'v1', targetHandle: 'voice' })
    expect(screen.getByLabelText('第 3 句台词')).toBeInTheDocument()
  })

  it('deletes a line together with its voice edge', async () => {
    withLines()
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '删除第 1 句' }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    const graph = savedGraph()
    expect(graph.nodes!.some((item) => item.id === 'a1')).toBe(false)
    expect(graph.edges!.some((item) => item.source === 'a1')).toBe(false)
    expect(graph.nodes!.some((item) => item.id === 'a2')).toBe(true)
  })

  it('reorders lines by rewriting order', async () => {
    withLines()
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '第 1 句下移' }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect([savedData('a1').order, savedData('a2').order]).toEqual([1, 0])
    expect((screen.getByLabelText('第 1 句台词') as HTMLTextAreaElement).value).toBe('那一夜')
  })

  it('falls back to the narrator when the shot has no character voice yet', async () => {
    setupApi({ nodes: graphNodes().filter((item) => item.id !== 'char'), edges: graphEdges().filter((item) => item.source !== 'char') })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '添加台词' }))
    expect(await screen.findByLabelText('第 1 句说话人')).toHaveValue('旁白')
  })
})

describe('StoryboardPage video generation mode', () => {
  const videoQuote = (_id: string, request: { reference_artifact_ids?: string[] }) => Promise.resolve({
    kind: 'video' as const, model: 'seedance', mode: request.reference_artifact_ids?.length ? 'reference' : 'first_frame',
    estimated_cost: request.reference_artifact_ids?.length ? 0.9 : 0.4,
  })

  it('switches to reference: character edges in, first frame edge out, image node kept, price follows the quote', async () => {
    api.quote.mockImplementation(videoQuote)
    renderPage()
    expect(await screen.findByRole('button', { name: '生成视频 · ¥0.400' })).toBeEnabled()
    expect(screen.getByRole('radio', { name: /从首帧画面生成/ })).toBeChecked()
    expect(screen.getByText('画面里的角色更像，但不能同时用首帧，价格更高')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: /直接参考角色图/ }))
    expect(screen.getByRole('radio', { name: /直接参考角色图/ })).toBeChecked()
    expect(await screen.findByRole('button', { name: '生成视频 · ¥0.900' })).toBeEnabled()
    const request = api.quote.mock.calls[api.quote.mock.calls.length - 1][1]
    expect(request).toMatchObject({ kind: 'video', reference_artifact_ids: ['c1'], first_frame_artifact_id: null })

    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    const [, graph, revision] = lastSave()
    expect(revision).toBe(3)
    const intoVideo = graph.edges!.filter((item) => item.target === 'v1').map((item) => [item.source, item.targetHandle])
    expect(intoVideo).toEqual([['char', 'reference']])
    expect(graph.nodes!.some((item) => item.id === 'k1')).toBe(true)
    expect(savedData('v1').parked_first_frame).toBe('k1')
    // 仍是两个镜头：被摘掉连线的首帧图片没有变成新镜头
    expect(screen.getByText('2 个镜头 · 约 9 秒 · 拖动可调整顺序')).toBeInTheDocument()
  })

  it('submits the reference request when generating, and switching back restores the first frame', async () => {
    api.quote.mockImplementation(videoQuote)
    renderPage()
    fireEvent.click(await screen.findByRole('radio', { name: /直接参考角色图/ }))
    fireEvent.click(await screen.findByRole('button', { name: '生成视频 · ¥0.900' }))
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1))
    expect(api.run.mock.calls[0][1]).toBe('v1')
    expect(api.run.mock.calls[0][2]).toMatchObject({ kind: 'video', reference_artifact_ids: ['c1'], first_frame_artifact_id: null })

    fireEvent.click(screen.getByRole('radio', { name: /从首帧画面生成/ }))
    expect(await screen.findByRole('button', { name: '生成视频 · ¥0.400' })).toBeEnabled()
    await waitFor(() => expect(api.save.mock.calls.length).toBeGreaterThan(0), { timeout: 3000 })
    await waitFor(() => {
      const intoVideo = lastSave()[1].edges!.filter((item) => item.target === 'v1').map((item) => [item.source, item.targetHandle])
      expect(intoVideo).toEqual([['k1', 'first_frame']])
    }, { timeout: 3000 })
    expect(savedData('v1')).not.toHaveProperty('parked_first_frame')
  })

  it('disables the reference option when the shot has no cast, and says what to do', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '镜头 2：未生成' }))
    const option = await screen.findByRole('radio', { name: /直接参考角色图/ })
    expect(option).toBeDisabled()
    expect(screen.getByRole('radio', { name: /从首帧画面生成/ })).toBeChecked()
    expect(screen.getByText('先在“出镜角色”里选角色')).toBeInTheDocument()
  })

  it('shows the reference mode when the canvas already feeds characters into the video', async () => {
    const nodes = graphNodes()
    nodes[2].data.parked_first_frame = 'k1'
    setupApi({ nodes, edges: [link('char', 'k1', 'reference'), link('char', 'v1', 'reference'), link('k2', 'v2', 'first_frame')] })
    renderPage()
    expect(await screen.findByRole('radio', { name: /直接参考角色图/ })).toBeChecked()
    expect(screen.getByText('2 个镜头 · 约 9 秒 · 拖动可调整顺序')).toBeInTheDocument()
  })
  it('after all reference edges are deleted it still shows reference mode, blocks generating, and can restore the first frame', async () => {
    const nodes = graphNodes()
    nodes[2].data.parked_first_frame = 'k1'
    setupApi({ nodes, edges: [link('char', 'k1', 'reference'), link('k2', 'v2', 'first_frame')] })
    renderPage()
    expect(await screen.findByRole('radio', { name: /直接参考角色图/ })).toBeChecked()
    expect(screen.getAllByText('还没有连角色参考图：连上角色，或改回「从首帧画面生成」').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /^生成视频/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('radio', { name: /从首帧画面生成/ }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(lastSave()[1].edges!.filter((item) => item.target === 'v1').map((item) => [item.source, item.targetHandle])).toEqual([['k1', 'first_frame']])
    expect(savedData('v1')).not.toHaveProperty('parked_first_frame')
  })

  it('names the characters that will not be used beyond nine', async () => {
    const chars = Array.from({ length: 11 }, (_, index) => node(`c${index}`, 'image', { role: 'character', character: `角色${index}` }, -400 + index))
    setupApi({
      nodes: [...chars, ...graphNodes().slice(1)],
      edges: [...chars.map((item) => link(item.id, 'k1', 'reference')), link('k1', 'v1', 'first_frame'), link('k2', 'v2', 'first_frame')],
    })
    renderPage()
    expect(await screen.findByText('参考图最多 9 张，只会用前 9 个角色（未使用：角色9、角色10）')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /直接参考角色图/ }))
    expect(await screen.findByText('本镜头参考 9 张角色图')).toBeInTheDocument()
    expect(screen.getByText('参考图最多 9 张，只会用前 9 个角色（未使用：角色9、角色10）')).toBeInTheDocument()
  })

  it('shows the duration reason from the quote instead of generating', async () => {
    const reason = '直接参考角色图需要 4–15 秒的镜头（现在是 3 秒）：把镜头时长改到这个范围'
    api.quote.mockRejectedValue(new Error(reason))
    renderPage()
    fireEvent.click(await screen.findByRole('radio', { name: /直接参考角色图/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: /^生成视频/ })).toBeDisabled())
    expect(screen.getAllByText(reason).length).toBeGreaterThan(0)
  })

describe('StoryboardPage cost and reference durations', () => {
  const costOf = (total: number, is_estimate = false) => ({ actual: is_estimate ? 0 : total, estimated: is_estimate ? total : 0, total, is_estimate })

  it('shows what each shot has cost, with 约 for estimates and nothing when free', async () => {
    const runs = runsValue()
    runs.shot_costs = { v1: costOf(0.21), v2: costOf(1.5, true) }
    setupApi({ runs })
    renderPage()
    expect(await screen.findByText('已花 ¥0.210')).toBeInTheDocument()
    expect(screen.getByText('已花约 ¥1.50')).toBeInTheDocument()
  })

  it('hides the cost when nothing was spent (mock is 0)', async () => {
    const runs = runsValue()
    runs.shot_costs = { v1: costOf(0), v2: costOf(0) }
    setupApi({ runs })
    renderPage()
    await screen.findByText('2 个镜头 · 约 9 秒 · 拖动可调整顺序')
    expect(screen.queryByText(/已花/)).not.toBeInTheDocument()
  })

  it('offers 4–15 seconds in reference mode (no 3) and the usual range otherwise', async () => {
    renderPage()
    const select = await screen.findByLabelText('时长')
    expect(within(select).getByRole('option', { name: '3 秒' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /直接参考角色图/ }))
    await waitFor(() => expect(within(screen.getByLabelText('时长')).queryByRole('option', { name: '3 秒' })).not.toBeInTheDocument())
    const options = within(screen.getByLabelText('时长')).getAllByRole('option').map((item) => item.textContent)
    expect(options[0]).toBe('4 秒')
    expect(options[options.length - 1]).toBe('15 秒')
  })

  it('moves an illegal duration to the nearest legal one when switching to reference mode, with a notice', async () => {
    const nodes = graphNodes()
    nodes[2].data.duration = 3
    setupApi({ nodes })
    renderPage()
    fireEvent.click(await screen.findByRole('radio', { name: /直接参考角色图/ }))
    expect(await screen.findByText('参考图模式只支持 4–15 秒，已把时长从 3 秒调到 4 秒')).toBeInTheDocument()
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(savedData('v1')).toMatchObject({ duration: 4 })
    expect(screen.getByLabelText('时长')).toHaveValue('4')
  })

  it('preserves saved 15 seconds when switching back to first-frame mode', async () => {
    const nodes = graphNodes()
    nodes[2].data = { ...nodes[2].data, duration: 15, parked_first_frame: 'k1' }
    setupApi({ nodes, edges: [link('char', 'k1', 'reference'), link('char', 'v1', 'reference'), link('k2', 'v2', 'first_frame')] })
    api.models.mockResolvedValue({ image: [], video: [{ slug: 'h', name: 'H', model: 'h', provider: 'x', modes: ['frame', 'reference'], pricing: {}, min_duration: 4, max_duration: 15 }], defaults: { image: '', video_preview: 'h', video_final: 'h' } })
    renderPage()
    fireEvent.click(await screen.findByRole('radio', { name: /从首帧画面生成/ }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(savedData('v1').duration).toBe(15)
    expect(screen.getByLabelText('时长')).toHaveValue('15')
    expect(screen.getByRole('option', { name: '15 秒' })).toBeInTheDocument()
    expect(screen.queryByText(/参考图模式只支持/)).not.toBeInTheDocument()
  })

  it('uses the routed deployment min/max for reference mode when the models list has them', async () => {
    const nodes = graphNodes()
    nodes[2].data.duration = 12
    setupApi({ nodes })
    api.models.mockResolvedValue({
      image: [],
      video: [
        { slug: 'h', name: 'H', model: 'h', provider: 'x', modes: ['reference'], pricing: {}, min_duration: 5, max_duration: 10 },
        { slug: 'other', name: 'O', model: 'o', provider: 'x', modes: ['frame'], pricing: {}, min_duration: 2, max_duration: 12 },
      ],
      defaults: { image: 'seedream', video_preview: 'h', video_final: 'h' },
    })
    renderPage()
    fireEvent.click(await screen.findByRole('radio', { name: /直接参考角色图/ }))
    expect(await screen.findByText('参考图模式只支持 5–10 秒，已把时长从 12 秒调到 10 秒')).toBeInTheDocument()
    const options = within(screen.getByLabelText('时长')).getAllByRole('option').map((item) => item.textContent)
    expect([options[0], options[options.length - 1]]).toEqual(['5 秒', '10 秒'])
  })
})

describe('StoryboardPage cast picker', () => {
  const boy = () => node('boy', 'image', { role: 'character', character: '书生', label: '角色 · 书生' }, -300)
  const savedEdges = () => lastSave()[1].edges!.map((item) => `${item.source}>${item.target}:${item.targetHandle}`)

  it('lists the episode characters as toggle chips with the current shot selection', async () => {
    setupApi({ nodes: [...graphNodes(), boy()] })
    renderPage()
    const fox = await screen.findByRole('button', { name: '狐妖' })
    expect(fox).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '书生' })).toHaveAttribute('aria-pressed', 'false')
    expect(fox.querySelector('img')).not.toBeNull()  // 基础造型缩略图
  })

  it('selects a character: base look gets a reference edge to the key frame, other shots untouched', async () => {
    setupApi({ nodes: [...graphNodes(), boy()] })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '书生' }))
    expect(screen.getByRole('button', { name: '书生' })).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(lastSave()[2]).toBe(3)
    expect(savedEdges()).toEqual(['char>k1:reference', 'k1>v1:first_frame', 'k2>v2:first_frame', 'boy>k1:reference'])
  })

  it('deselects a character: removes its reference edges to this shot only and keeps it a character', async () => {
    const nodes = [...graphNodes(), boy()]
    setupApi({ nodes, edges: [...graphEdges(), link('char', 'k2', 'reference')] })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '狐妖' }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(savedEdges()).toEqual(['k1>v1:first_frame', 'k2>v2:first_frame', 'char>k2:reference'])
    expect(savedData('char').role).toBe('character')
  })

  it('also wires the video in reference mode, and the quote follows', async () => {
    const nodes = [...graphNodes(), boy()]
    nodes[2].data.parked_first_frame = 'k1'
    setupApi({ nodes, edges: [link('char', 'k1', 'reference'), link('char', 'v1', 'reference'), link('k2', 'v2', 'first_frame')] })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '书生' }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(savedEdges()).toEqual(expect.arrayContaining(['boy>k1:reference', 'boy>v1:reference']))
    expect(screen.getByText('本镜头参考 2 张角色图')).toBeInTheDocument()
  })

  it('refuses a fifth character on the key frame with a message and writes nothing', async () => {
    const extra = ['a', 'b', 'c'].map((id) => node(id, 'image', { role: 'character', character: `甲${id}` }, -300))
    setupApi({ nodes: [...graphNodes(), ...extra, boy()], edges: [...graphEdges(), ...extra.map((item) => link(item.id, 'k1', 'reference'))] })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '书生' }))
    expect(await screen.findByText('这个镜头的画面最多参考 4 个角色')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '书生' })).toHaveAttribute('aria-pressed', 'false')
    await new Promise((resolve) => setTimeout(resolve, 1000))
    expect(api.save).not.toHaveBeenCalled()
  })

  it('shows the empty state with a link to the characters step', async () => {
    setupApi({ nodes: graphNodes().filter((item) => item.id !== 'char'), edges: [link('k1', 'v1', 'first_frame'), link('k2', 'v2', 'first_frame')] })
    renderPage()
    expect(await screen.findByText(/还没有角色：/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '去「角色」步骤添加' })).toHaveAttribute('href', '/studio/c1/characters')
  })

  it('enables the reference option once a character is picked', async () => {
    setupApi({ nodes: [...graphNodes(), boy()] })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '镜头 2：未生成' }))
    expect(await screen.findByRole('radio', { name: /直接参考角色图/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '书生' }))
    expect(screen.getByRole('radio', { name: /直接参考角色图/ })).toBeEnabled()
  })
})
})
