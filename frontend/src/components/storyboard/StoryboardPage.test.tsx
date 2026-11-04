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

  it('greys out characters and dialogue until later steps ship', async () => {
    renderPage()
    await screen.findByLabelText('画面描述')
    expect(screen.getByText('第 4 步上线后可编辑')).toBeInTheDocument()
    expect(screen.getByText('第 6 步上线后可编辑')).toBeInTheDocument()
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
