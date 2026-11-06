import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConflictError, nodeCanvasApi } from '../../api/nodeCanvas'
import { resetVoiceCache } from '../voice/useVoiceList'
import NodeCanvasPage from './NodeCanvasPage'
import { thumbStyle } from './ShotGroupNode'

vi.mock('../../api/nodeCanvas', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/nodeCanvas')>()
  return {
    ...actual,
    nodeCanvasApi: {
      get: vi.fn(), models: vi.fn(), voices: vi.fn(), runs: vi.fn(), save: vi.fn(), quote: vi.fn(), run: vi.fn(), upload: vi.fn(), split: vi.fn(), rename: vi.fn(), cancelJob: vi.fn(),
      batchQuote: vi.fn(), batchRun: vi.fn(), batchList: vi.fn(), batchGet: vi.fn(), batchCancel: vi.fn(),
    },
  }
})
vi.mock('../ui/Toast', () => ({ toast: vi.fn() }))
vi.mock('../common/ProtectedMedia', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../common/ProtectedMedia')>()
  return { ...actual, ProtectedAudio: ({ src }: { src: string }) => <audio data-testid="line-audio" src={src} /> }
})

beforeAll(() => {
  // React Flow 在 jsdom 里需要这些浏览器 API；未测量尺寸的节点是 visibility:hidden，按 label / 文本查询
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver
  // @ts-expect-error jsdom 没有 DOMMatrixReadOnly
  globalThis.DOMMatrixReadOnly = class { m22 = 1; constructor() {} }
})

const api = vi.mocked(nodeCanvasApi)

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  resetVoiceCache()
  api.voices.mockResolvedValue([{ id: 'Cherry', name: '芊悦', gender: 'female', style: '阳光积极', sample_text: '你好', sample_url: null }])
  api.get.mockResolvedValue({
    id: 'c1', title: '雨夜', aspect_ratio: '9:16', node_count: 1, updated_at: null, created_at: null, revision: 3,
    graph: { nodes: [{ id: 'text-1', type: 'text', position: { x: 0, y: 0 }, data: { label: '想法', text: '雨夜便利店' } }], edges: [], viewport: { x: 0, y: 0, zoom: 1 } },
  })
  api.models.mockResolvedValue({ image: [{ slug: 'seedream', name: 'Seedream', model: 'm', provider: '火山', modes: [], pricing: {} }], video: [], defaults: { image: 'seedream', video_preview: '', video_final: '' } })
  api.runs.mockResolvedValue({ nodes: {}, active_jobs: 0, spent: 1.5 })
  api.save.mockResolvedValue({ revision: 4 })
  api.quote.mockResolvedValue({ kind: 'image', model: 'seedream', estimated_cost: 0.2 })
  api.run.mockResolvedValue({ job_id: 'j1', status: 'pending' })
  api.batchList.mockResolvedValue([])
})

/** 工具栏的下拉菜单：先点开，再点菜单项 */
function chooseMenu(menu: string, item: string) {
  fireEvent.click(screen.getByRole('button', { name: menu }))
  fireEvent.click(screen.getByRole('menuitem', { name: item }))
}

function renderPage(entry = '/') {
  return render(<MemoryRouter initialEntries={[entry]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><NodeCanvasPage canvasId="c1" /></MemoryRouter>)
}

describe('NodeCanvasPage', () => {
  it('selects the node named by ?node= when the canvas opens', async () => {
    const { container } = renderPage('/?node=text-1')
    await waitFor(() => expect(container.querySelector('.nc-node--selected')).not.toBeNull())
    expect(screen.getByDisplayValue('想法').closest('.nc-node')).toHaveClass('nc-node--selected')
  })

  it('selects nothing for an unknown ?node=', async () => {
    const { container } = renderPage('/?node=nope')
    await screen.findByDisplayValue('想法')
    expect(container.querySelector('.nc-node--selected')).toBeNull()
  })

  it('loads the graph and shows spend', async () => {
    renderPage()
    expect(await screen.findByDisplayValue('雨夜便利店')).toBeInTheDocument()
    // 画布名称改到项目顶栏，工具栏不再有返回 / 标题输入 / 预算
    expect(screen.queryByLabelText('画布名称')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('返回项目')).not.toBeInTheDocument()
    expect(screen.queryByText(/今日云端/)).not.toBeInTheDocument()
    expect(await screen.findByText('已花费 ¥1.50')).toBeInTheDocument()
  })

  it('autosaves edits with the current revision', async () => {
    renderPage()
    fireEvent.change(await screen.findByDisplayValue('雨夜便利店'), { target: { value: '雨夜便利店，一只小猫' } })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    const [id, graph, revision] = api.save.mock.calls[api.save.mock.calls.length - 1]
    expect(id).toBe('c1')
    expect(revision).toBe(3)
    expect(graph.nodes?.[0].data.text).toBe('雨夜便利店，一只小猫')
  })

  it('adds an image node, saves, then runs it with the merged prompt', async () => {
    renderPage()
    await screen.findByDisplayValue('雨夜便利店')
    chooseMenu('更多', '新建图片节点')
    fireEvent.change(await screen.findByLabelText('提示词'), { target: { value: '女店员' } })
    const run = (await screen.findByText('生成')).closest('button')!
    await waitFor(() => expect(run).not.toBeDisabled())
    await act(async () => { fireEvent.click(run) })
    await waitFor(() => expect(api.run).toHaveBeenCalled())
    expect(api.save).toHaveBeenCalled()
    const [, nodeId, request] = api.run.mock.calls[0]
    expect(nodeId).toMatch(/^image-/)
    expect(request).toMatchObject({ kind: 'image', prompt: '女店员', aspect_ratio: '9:16', count: 1 })
    expect(await screen.findByText('预计 ¥0.200')).toBeInTheDocument()
  })

  it('places a Copilot split on the canvas', async () => {
    api.split.mockResolvedValue({ characters: [{ name: '林夏', description: '短发' }], shots: [{ title: '推门', prompt: '推门进店', characters: ['林夏'], duration_s: 4 }] })
    renderPage()
    await screen.findByDisplayValue('雨夜便利店')
    fireEvent.click(screen.getByRole('button', { name: /Copilot/ }))
    fireEvent.change(screen.getByRole('textbox', { name: '想法' }), { target: { value: '雨夜便利店' } })
    fireEvent.click(screen.getByRole('button', { name: /拆分镜头/ }))
    fireEvent.click(await screen.findByRole('button', { name: '放到画布' }))
    expect(await screen.findByDisplayValue('角色 · 林夏')).toBeInTheDocument()
    expect(screen.getByDisplayValue('1 · 推门')).toBeInTheDocument()
    expect(api.split).toHaveBeenCalledWith('c1', '雨夜便利店', 4)
  })

  function withImageJob(status: string, error = '') {
    api.get.mockResolvedValue({
      id: 'c1', title: '雨夜', aspect_ratio: '9:16', node_count: 1, updated_at: null, created_at: null, revision: 3,
      graph: { nodes: [{ id: 'img-1', type: 'image', position: { x: 0, y: 0 }, data: { label: '定妆', prompt: '林夏' } }], edges: [] },
    })
    api.runs.mockResolvedValue({
      nodes: { 'img-1': { jobs: [{ id: 'job-1', status, progress: 40, queue_position: 0, error, model: 'seedream', created_at: null }], results: [] } },
      active_jobs: status === 'running' ? 1 : 0, spent: 0,
    })
  }

  it('cancels a running node job', async () => {
    withImageJob('running')
    api.cancelJob.mockResolvedValue({})
    renderPage()
    const cancel = (await screen.findByText('取消')).closest('button')!
    await act(async () => { fireEvent.click(cancel) })
    expect(api.cancelJob).toHaveBeenCalledWith('job-1')
    expect(screen.queryByText('生成')).not.toBeInTheDocument()
  })

  it('retries a failed node job with the current inputs', async () => {
    withImageJob('failed', 'provider timeout')
    renderPage()
    expect(await screen.findByText('provider timeout')).toBeInTheDocument()
    const retry = (await screen.findByText('重试')).closest('button')!
    await waitFor(() => expect(retry).not.toBeDisabled())
    await act(async () => { fireEvent.click(retry) })
    await waitFor(() => expect(api.run).toHaveBeenCalled())
    expect(api.run.mock.calls[0][1]).toBe('img-1')
    expect(api.run.mock.calls[0][2]).toMatchObject({ kind: 'image', prompt: '林夏' })
  })

  const batchQuote = {
    revision: 3,
    nodes: [
      { node_id: 'img-1', label: '定妆', kind: 'image' as const, status: 'pending' as const, estimated_cost: 0.2, unavailable: false, reason: '' },
      { node_id: 'vid-1', label: '推门', kind: 'video' as const, status: 'pending' as const, estimated_cost: null, unavailable: true, reason: '未配置价格' },
    ],
    estimated_total: 0.2, billable_total: 0.2, runnable_count: 2, waiting_count: 0, blocking_count: 0,
    unavailable_nodes: [{ node_id: 'vid-1', label: '推门', reason: '未配置价格', runs: true, blocks_batch: false }],
    budget: { cap: 20, remaining: 15 }, exceeds_budget: false,
  }
  const batchRun = { id: 'b1', status: 'running' as const, node_ids: ['img-1', 'vid-1'], nodes: {}, total: 2, completed: 0, failed: 0, estimated_total: 0.2 }

  it('uses a fresh client_request_id per click and reuses it when the request is retried', async () => {
    withImageJob('succeeded')
    api.run.mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValue({ job_id: 'j2', status: 'pending' })
    renderPage()
    const click = async () => {
      const button = (await screen.findByText('生成')).closest('button')!
      await waitFor(() => expect(button).not.toBeDisabled())
      await act(async () => { fireEvent.click(button) })
    }
    await click()
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(2))
    const [first, retry] = api.run.mock.calls.map((call) => call[2].client_request_id)
    expect(first).toBeTruthy()
    expect(retry).toBe(first)
    await click()
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(3))
    expect(api.run.mock.calls[2][2].client_request_id).not.toBe(first)
  })

  it('quotes first, then submits the whole canvas only after confirmation', async () => {
    withImageJob('succeeded')
    api.batchQuote.mockResolvedValue(batchQuote)
    api.batchRun.mockRejectedValueOnce(new Error('网络错误')).mockResolvedValue(batchRun)
    renderPage()
    fireEvent.click(await screen.findByText('全部运行'))
    expect(await screen.findByText(/预计合计/)).toHaveTextContent('¥0.200')
    expect(screen.getByText('价格未知（未计入合计）')).toBeInTheDocument()
    expect(screen.getByText('未配置价格')).toBeInTheDocument()
    expect(api.batchQuote).toHaveBeenCalledWith('c1', { scope: 'all', node_id: undefined })
    expect(api.batchRun).not.toHaveBeenCalled()

    fireEvent.click(screen.getByText('确认运行'))
    expect(await screen.findByText('网络错误')).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByText('确认运行')) })
    await waitFor(() => expect(api.batchRun).toHaveBeenCalledTimes(2))
    const [first, second] = api.batchRun.mock.calls.map((call) => call[1])
    expect(first).toMatchObject({ scope: 'all', skip_succeeded: true, quoted_revision: 3 })
    expect(second.client_request_id).toBe(first.client_request_id)  // 重试复用，后端返回同一个批次
    expect(await screen.findByRole('status')).toHaveTextContent('批量 0/2 完成')
  })

  it('offers staged entries that quote and submit with the right kinds', async () => {
    withImageJob('succeeded')
    api.batchQuote.mockResolvedValue(batchQuote)
    api.batchRun.mockResolvedValue({ ...batchRun, kinds: ['image'] })
    renderPage()
    await screen.findByText('全部运行')
    chooseMenu('分段生成', '生成全部画面')
    await screen.findByText(/预计合计/)
    expect(api.batchQuote).toHaveBeenLastCalledWith('c1', { scope: 'all', node_id: undefined, kinds: ['image'] })
    await act(async () => { fireEvent.click(screen.getByText('确认运行')) })
    await waitFor(() => expect(api.batchRun).toHaveBeenCalledTimes(1))
    expect(api.batchRun.mock.calls[0][1]).toMatchObject({ scope: 'all', kinds: ['image'] })
    expect(await screen.findByRole('status')).toHaveTextContent('画面批次 0/2 完成')
  })

  it('shows the video prompt after the image batch finishes and opens the video quote', async () => {
    withImageJob('succeeded')
    api.batchList.mockResolvedValue([{ ...batchRun, kinds: ['image'] }])
    api.batchGet.mockResolvedValue({ ...batchRun, kinds: ['image'], status: 'succeeded', completed: 2 })
    api.batchQuote.mockResolvedValue(batchQuote)
    renderPage()
    expect(screen.queryByText(/画面已完成/)).not.toBeInTheDocument()
    expect(await screen.findByText(/画面已完成：挑选满意的图/, undefined, { timeout: 5000 })).toBeInTheDocument()
    fireEvent.click(screen.getByText('生成全部视频', { selector: '.nc-batch-hint button' }))
    await screen.findByText(/预计合计/)
    expect(api.batchQuote).toHaveBeenLastCalledWith('c1', { scope: 'all', node_id: undefined, kinds: ['video'] })
  })

  const videoNode = (reason: string, blocks: boolean) => ({ node_id: 'vid-1', label: '推门', reason, runs: false, blocks_batch: blocks })

  it('says nothing can run and disables confirm when every video waits for its image', async () => {
    withImageJob('succeeded')
    api.batchQuote.mockResolvedValue({
      ...batchQuote, estimated_total: 0, runnable_count: 0, waiting_count: 1,
      unavailable_nodes: [videoNode('上游图片还没有结果', false)],
    })
    renderPage()
    await screen.findByText('全部运行')
    chooseMenu('分段生成', '生成全部视频')
    expect(await screen.findByText('没有可以运行的节点（1 个缺上游图片，先跳过）')).toBeInTheDocument()
    expect(screen.queryByText(/将运行/)).not.toBeInTheDocument()
    expect(screen.getByText('这些节点暂时无法运行（未计入合计）')).toBeInTheDocument()
    expect(screen.getByText('确认运行').closest('button')).toBeDisabled()
  })

  it('falls back to the old counting when the server sends an older quote shape', async () => {
    withImageJob('succeeded')
    const { runnable_count: _r, waiting_count: _w, blocking_count: _b, ...old } = batchQuote
    api.batchQuote.mockResolvedValue({ ...old, unavailable_nodes: [{ node_id: 'vid-1', label: '推门', reason: '未配置价格' }] })
    renderPage()
    fireEvent.click(await screen.findByText('全部运行'))
    expect(await screen.findByText(/将运行 2 个节点/)).toBeInTheDocument()
    expect(screen.getByText('价格未知（未计入合计）')).toBeInTheDocument()
    expect(screen.getByText('确认运行').closest('button')).not.toBeDisabled()
  })

  it('counts only runnable nodes and blocks confirm while a node must be fixed first', async () => {
    withImageJob('succeeded')
    api.batchQuote.mockResolvedValueOnce({ ...batchQuote, runnable_count: 1, waiting_count: 1 })
    renderPage()
    await screen.findByText('全部运行')
    chooseMenu('分段生成', '生成全部视频')
    expect(await screen.findByText(/将运行 1 个节点（1 个缺上游图片，先跳过）/)).toBeInTheDocument()
    expect(screen.getByText('确认运行').closest('button')).not.toBeDisabled()

    api.batchQuote.mockResolvedValueOnce({
      ...batchQuote, runnable_count: 1, blocking_count: 1,
      unavailable_nodes: [videoNode('还没有设置视频模型', true)],
    })
    fireEvent.click(screen.getByLabelText('关闭批量运行'))
    fireEvent.click(screen.getByText('全部运行'))
    expect(await screen.findByText('有 1 个节点需要先处理，处理后才能运行')).toBeInTheDocument()
    expect(screen.getByText('还没有设置视频模型')).toBeInTheDocument()
    expect(screen.getByText('确认运行').closest('button')).toBeDisabled()
  })

  it('restores a finished image batch after reopening, keeps the hint until dismissed or replaced', async () => {
    withImageJob('succeeded')
    api.batchList.mockResolvedValue([{ ...batchRun, kinds: ['image'], status: 'failed', completed: 1, failed: 1 }])
    api.batchQuote.mockResolvedValue(batchQuote)
    api.batchRun.mockResolvedValue({ ...batchRun, id: 'b2', kinds: ['video'] })
    renderPage()
    expect(await screen.findByText(/画面已完成：挑选满意的图/)).toBeInTheDocument()
    expect(screen.getByText(/1 失败/)).toBeInTheDocument()
    expect(api.batchGet).not.toHaveBeenCalled()  // 终态不轮询
    fireEvent.click(screen.getByLabelText('关闭提示'))
    expect(screen.queryByText(/画面已完成/)).not.toBeInTheDocument()
  })

  it('hides the image-batch hint once a newer batch starts', async () => {
    withImageJob('succeeded')
    api.batchList.mockResolvedValue([{ ...batchRun, kinds: ['image'], status: 'succeeded', completed: 2 }])
    api.batchQuote.mockResolvedValue(batchQuote)
    api.batchRun.mockResolvedValue({ ...batchRun, id: 'b2', kinds: ['video'] })
    renderPage()
    fireEvent.click(await screen.findByText('生成全部视频', { selector: '.nc-batch-hint button' }))
    await screen.findByText(/预计合计/)
    await act(async () => { fireEvent.click(screen.getByText('确认运行')) })
    await waitFor(() => expect(screen.queryByText(/画面已完成/)).not.toBeInTheDocument())
    expect(await screen.findByText(/视频批次 0\/2 完成/)).toBeInTheDocument()
  })

  it('does not submit when the quote exceeds the remaining budget', async () => {
    withImageJob('succeeded')
    api.batchQuote.mockResolvedValue({ ...batchQuote, exceeds_budget: true, budget: { cap: 20, remaining: 0.1 } })
    renderPage()
    fireEvent.click(await screen.findByText('全部运行'))
    expect(await screen.findByText(/超出额度无法运行/)).toBeInTheDocument()
    expect(screen.getByText('确认运行').closest('button')).toBeDisabled()
  })

  it('runs a node and its downstream from the node, and cancels the batch', async () => {
    withImageJob('succeeded')
    api.batchQuote.mockResolvedValue(batchQuote)
    api.batchRun.mockResolvedValue(batchRun)
    api.batchCancel.mockResolvedValue({ ...batchRun, status: 'canceled' })
    renderPage()
    fireEvent.click(await screen.findByLabelText('运行此节点及下游'))
    await screen.findByText(/预计合计/)
    expect(api.batchQuote).toHaveBeenCalledWith('c1', { scope: 'downstream', node_id: 'img-1' })
    await act(async () => { fireEvent.click(screen.getByText('确认运行')) })
    await waitFor(() => expect(api.batchRun).toHaveBeenCalled())
    expect(api.batchRun.mock.calls[0][1]).toMatchObject({ scope: 'downstream', node_id: 'img-1' })
    await act(async () => { fireEvent.click(await screen.findByText('取消', { selector: '.nc-batch-status button' })) })
    expect(api.batchCancel).toHaveBeenCalledWith('c1', 'b1')
    expect(await screen.findByRole('status')).toHaveTextContent('已取消')
  })

  it('aborts the batch quote with a visible error when saving the canvas fails', async () => {
    api.save.mockRejectedValue(new Error('服务器错误'))
    api.batchQuote.mockResolvedValue(batchQuote)
    renderPage()
    fireEvent.change(await screen.findByDisplayValue('雨夜便利店'), { target: { value: '改了一半' } })
    fireEvent.click(screen.getByText('全部运行'))
    expect(await screen.findByText('画布保存失败，已取消操作，请稍后重试')).toBeInTheDocument()
    expect(api.batchQuote).not.toHaveBeenCalled()
    expect(api.batchRun).not.toHaveBeenCalled()
  })

  it('re-quotes automatically when the canvas changed after the quote was shown', async () => {
    withImageJob('succeeded')
    api.batchQuote.mockResolvedValueOnce(batchQuote).mockResolvedValue({ ...batchQuote, revision: 4, estimated_total: 0.5 })
    api.batchRun.mockRejectedValueOnce(new ConflictError('画布在报价后被修改')).mockResolvedValue(batchRun)
    renderPage()
    fireEvent.click(await screen.findByText('全部运行'))
    await screen.findByText(/预计合计/)
    await act(async () => { fireEvent.click(screen.getByText('确认运行')) })
    await waitFor(() => expect(api.batchQuote).toHaveBeenCalledTimes(2))
    expect(await screen.findByText(/已按最新内容重新报价/)).toBeInTheDocument()
    expect(await screen.findByText(/预计合计/)).toHaveTextContent('¥0.500')
    expect(api.batchRun).toHaveBeenCalledTimes(1)  // 重新报价后要用户再确认
    await act(async () => { fireEvent.click(screen.getByText('确认运行')) })
    await waitFor(() => expect(api.batchRun).toHaveBeenCalledTimes(2))
    expect(api.batchRun.mock.calls[1][1]).toMatchObject({ quoted_revision: 4 })
  })

  it('ignores a late poll response for a batch that was canceled and replaced', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      withImageJob('succeeded')
      const batchA = { ...batchRun, id: 'a' }
      const batchB = { ...batchRun, id: 'b', total: 7 }
      let lateA: (value: unknown) => void = () => undefined
      api.batchList.mockResolvedValue([batchA])
      api.batchGet.mockReturnValue(new Promise((resolve) => { lateA = resolve }) as never)
      api.batchCancel.mockResolvedValue({ ...batchA, status: 'canceled' })
      api.batchQuote.mockResolvedValue(batchQuote)
      api.batchRun.mockResolvedValue(batchB)
      renderPage()
      expect(await screen.findByRole('status')).toHaveTextContent('批量 0/2')
      await act(async () => { vi.advanceTimersByTime(3100) })
      await waitFor(() => expect(api.batchGet).toHaveBeenCalledWith('c1', 'a', expect.anything()))

      await act(async () => { fireEvent.click(screen.getByText('取消', { selector: '.nc-batch-status button' })) })
      fireEvent.click(await screen.findByText('全部运行'))
      await screen.findByText(/预计合计/)
      await act(async () => { fireEvent.click(screen.getByText('确认运行')) })
      expect(await screen.findByRole('status')).toHaveTextContent('批量 0/7')

      await act(async () => { lateA({ ...batchA, status: 'running', completed: 1 }) })
      expect(screen.getByRole('status')).toHaveTextContent('批量 0/7')
    } finally { vi.useRealTimers() }
  })

  it('explains a missing image model before generating', async () => {
    api.models.mockResolvedValue({ image: [], video: [], defaults: { image: '', video_preview: '', video_final: '' }, image_problem: '还没有可用的图片模型：去设置添加' })
    renderPage()
    await screen.findByDisplayValue('雨夜便利店')
    chooseMenu('更多', '新建图片节点')
    fireEvent.change(await screen.findByLabelText('提示词'), { target: { value: '女店员' } })
    expect(await screen.findByText('还没有可用的图片模型：去设置添加')).toBeInTheDocument()
    expect((await screen.findByText('生成')).closest('button')).toBeDisabled()
    expect(screen.getByLabelText('上传')).not.toBeDisabled()
  })

  // ---- 镜头分组 ----
  const shotGraph = (count: number, extra: { nodes?: unknown[]; edges?: unknown[] } = {}, spacing = 600) => {
    const nodes: unknown[] = []
    const edges: unknown[] = []
    for (let n = 1; n <= count; n++) {
      const y = (n - 1) * spacing
      nodes.push(
        { id: `k${n}`, type: 'image', position: { x: 0, y }, data: { label: `${n} · 开场 关键帧`, prompt: `画面${n}`, shot_size: '近景' } },
        { id: `v${n}`, type: 'video', position: { x: 400, y }, data: { label: `${n} · 开场`, prompt: `视频${n}`, duration: 4, order: n - 1 } },
      )
      edges.push({ id: `e${n}`, source: `k${n}`, target: `v${n}`, sourceHandle: 'out', targetHandle: 'first_frame' })
    }
    api.get.mockResolvedValue({
      id: 'c1', title: '雨夜', aspect_ratio: '9:16', node_count: nodes.length, updated_at: null, created_at: null, revision: 3,
      graph: { nodes: [...nodes, ...(extra.nodes || [])] as never, edges: [...edges, ...(extra.edges || [])] as never, viewport: { x: 0, y: 0, zoom: 1 } },
    })
  }

  it('wraps each shot in an expanded group by default and folds / unfolds it', async () => {
    shotGraph(2)
    renderPage()
    expect(await screen.findByLabelText('镜头 1（已展开）')).toBeInTheDocument()
    expect(screen.getByLabelText('镜头 2（已展开）')).toBeInTheDocument()
    expect(screen.getByText('镜头 1 · 近景 · 4 秒')).toBeInTheDocument()
    expect(screen.getByDisplayValue('画面1')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('折叠镜头 1'))
    expect(screen.queryByDisplayValue('画面1')).not.toBeInTheDocument()  // 成员隐藏
    expect(screen.queryByLabelText('镜头 1（已展开）')).not.toBeInTheDocument()
    expect(screen.getByLabelText('展开镜头 1')).toHaveTextContent('镜头 1 · 近景')
    expect(screen.getByLabelText('展开镜头 1')).toHaveTextContent('未生成')
    expect(screen.getByDisplayValue('画面2')).toBeInTheDocument()  // 另一组不受影响

    fireEvent.click(screen.getByLabelText('展开镜头 1'))
    expect(screen.getByDisplayValue('画面1')).toBeInTheDocument()
    expect(screen.getByLabelText('镜头 1（已展开）')).toBeInTheDocument()
  })

  it('collapses / expands every group from the toolbar and remembers it per canvas', async () => {
    shotGraph(2)
    const first = renderPage()
    await screen.findByLabelText('镜头 1（已展开）')
    fireEvent.click(screen.getByRole('button', { name: '全部折叠' }))
    expect(screen.getByLabelText('展开镜头 1')).toBeInTheDocument()
    expect(screen.getByLabelText('展开镜头 2')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('画面1')).not.toBeInTheDocument()
    expect(JSON.parse(window.localStorage.getItem('fishstudio.canvas.collapsed.c1') || '[]').sort()).toEqual(['v1', 'v2'])

    first.unmount()
    renderPage()  // 重新打开仍是折叠
    expect(await screen.findByLabelText('展开镜头 1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '全部展开' }))
    expect(screen.getByDisplayValue('画面1')).toBeInTheDocument()
    expect(screen.getByDisplayValue('画面2')).toBeInTheDocument()
  })

  it('folds everything by default when there are more than 4 shots', async () => {
    shotGraph(5)
    renderPage()
    expect(await screen.findByLabelText('展开镜头 5')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('画面1')).not.toBeInTheDocument()
  })

  it('expands the group that holds the ?node= target', async () => {
    shotGraph(5)
    renderPage('/?node=k3')
    expect(await screen.findByDisplayValue('画面3')).toBeInTheDocument()
    expect(screen.getByLabelText('镜头 3（已展开）')).toBeInTheDocument()
    expect(screen.getByLabelText('展开镜头 1')).toBeInTheDocument()  // 其他组仍折叠
    expect(screen.queryByDisplayValue('画面1')).not.toBeInTheDocument()
  })

  it('never saves the derived group nodes or the hidden flag', async () => {
    shotGraph(2)
    renderPage()
    await screen.findByLabelText('镜头 1（已展开）')
    fireEvent.click(screen.getByLabelText('折叠镜头 1'))
    fireEvent.change(screen.getByDisplayValue('画面2'), { target: { value: '改过的画面' } })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    const [, graph] = api.save.mock.calls[api.save.mock.calls.length - 1]
    expect(graph.nodes?.map((item) => item.id).sort()).toEqual(['k1', 'k2', 'v1', 'v2'])
    expect(JSON.stringify(graph)).not.toContain('shotGroup')
    expect(JSON.stringify(graph)).not.toContain('hidden')
    expect(graph.nodes?.find((item) => item.id === 'k2')?.data.prompt).toBe('改过的画面')
  })

  it('drops a stray shotGroup node that comes back in a graph', async () => {
    shotGraph(1, { nodes: [{ id: 'shot-group:v1', type: 'shotGroup', position: { x: 0, y: 0 }, data: {} }] })
    renderPage()
    await screen.findByLabelText('镜头 1（已展开）')
    fireEvent.change(screen.getByDisplayValue('画面1'), { target: { value: '改' } })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(JSON.stringify(api.save.mock.calls[0][1])).not.toContain('shotGroup')
  })

  it('adds a shot below the existing ones, expanded, and saves it', async () => {
    shotGraph(5)  // 5 个镜头默认全折叠
    renderPage()
    await screen.findByLabelText('展开镜头 5')
    fireEvent.click(screen.getByRole('button', { name: '添加镜头' }))
    expect(await screen.findByLabelText('镜头 6（已展开）')).toBeInTheDocument()
    expect(screen.getByLabelText('展开镜头 5')).toBeInTheDocument()
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    const [, graph] = api.save.mock.calls[api.save.mock.calls.length - 1]
    expect(graph.nodes).toHaveLength(12)
    const added = graph.nodes?.filter((item) => !/^[kv]\d$/.test(item.id)) || []
    expect(added.map((item) => item.type).sort()).toEqual(['image', 'video'])
    // 5 个镜头都折叠成小卡片：新镜头放在可见内容（最后一张卡片，底边 2400）之下，再加间距、标题栏和内边距
    expect(Math.min(...added.map((item) => item.position.y))).toBeGreaterThanOrEqual(2400 + 48 + 24 + 44)
    expect(graph.nodes?.find((item) => item.type === 'video' && item.data.order === 5)).toBeTruthy()
    expect(graph.edges?.some((item) => item.targetHandle === 'first_frame' && added.some((n) => n.id === item.target))).toBe(true)
  })

  it('tags character nodes and explains the dashed reference lines', async () => {
    shotGraph(1, {
      nodes: [{ id: 'c1', type: 'image', position: { x: -400, y: 0 }, data: { label: '角色 · 狐妖', role: 'character', character: '狐妖' } }],
      edges: [{ id: 'ec', source: 'c1', target: 'k1', sourceHandle: 'out', targetHandle: 'reference' }],
    })
    const { container } = renderPage()
    expect(await screen.findByText('虚线：角色参考')).toBeInTheDocument()
    expect(container.querySelectorAll('.nc-tag')).toHaveLength(1)
    expect(screen.getByDisplayValue('角色 · 狐妖').closest('.nc-node')).toContainElement(container.querySelector('.nc-tag') as HTMLElement)
    // 角色不属于任何镜头组
    expect(screen.getByLabelText('镜头 1（已展开）')).toBeInTheDocument()
    expect(container.querySelectorAll('.nc-group')).toHaveLength(1)
  })

  it('has a centred toolbar with the primary run button and no legend without characters', async () => {
    renderPage()
    await screen.findByDisplayValue('雨夜便利店')
    expect(screen.getByRole('button', { name: '全部运行' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加镜头' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '全部折叠' })).toBeDisabled()  // 还没有镜头
    expect(screen.queryByText('虚线：角色参考')).not.toBeInTheDocument()
  })

  it('clears the selection of nodes hidden by a fold, so Delete cannot remove them', async () => {
    shotGraph(2)
    renderPage('/?node=k1')
    await screen.findByLabelText('镜头 1（已展开）')
    await waitFor(() => expect(screen.getByDisplayValue('画面1').closest('.nc-node')).toHaveClass('nc-node--selected'))
    fireEvent.click(screen.getByRole('button', { name: '全部折叠' }))
    fireEvent.keyDown(document.body, { key: 'Delete', code: 'Delete' })
    fireEvent.keyUp(document.body, { key: 'Delete', code: 'Delete' })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    for (const call of api.save.mock.calls) expect(call[1].nodes?.map((item) => item.id).sort()).toEqual(['k1', 'k2', 'v1', 'v2'])
    expect(api.save.mock.calls.every((call) => call[1].edges?.length === 2)).toBe(true)
  })

  it('has our own fit-view button instead of the default one', async () => {
    const { container } = renderPage()
    await screen.findByDisplayValue('雨夜便利店')
    expect(container.querySelector('.react-flow__controls-fitview')).toBeNull()
    expect(screen.getByLabelText('适应画面')).toBeInTheDocument()
  })

  it('shows the dashed-line legend only when a character really has a reference edge', async () => {
    shotGraph(1, { nodes: [{ id: 'c1', type: 'image', position: { x: -400, y: 0 }, data: { label: '角色 · 狐妖', role: 'character', character: '狐妖' } }] })
    renderPage()
    await screen.findByLabelText('镜头 1（已展开）')
    expect(screen.queryByText('虚线：角色参考')).not.toBeInTheDocument()
  })

  it('tidies the layout and saves the new positions', async () => {
    shotGraph(2)
    renderPage()
    await screen.findByLabelText('镜头 1（已展开）')
    chooseMenu('更多', '整理布局')
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    const [, graph] = api.save.mock.calls[api.save.mock.calls.length - 1]
    const at = (id: string) => graph.nodes!.find((item) => item.id === id)!.position
    expect(at('k1').x).toBeLessThan(at('v1').x)
    expect(at('k2').y).toBeGreaterThan(at('k1').y + 500)
  })

  it('shows a clear progress status on groups instead of repeating the shot size', async () => {
    shotGraph(2)
    api.runs.mockResolvedValue({
      nodes: { v1: { jobs: [{ id: 'j', status: 'running', progress: 60, queue_position: 0, error: '', model: '', created_at: null }], results: [] } },
      active_jobs: 1, spent: 0,
    })
    renderPage()
    expect(await screen.findByText('视频生成中 60%')).toBeInTheDocument()
    expect(screen.getByText('未生成', { selector: '.nc-group__state' })).toBeInTheDocument()
    expect(screen.queryByText('近景', { selector: '.nc-group__state' })).not.toBeInTheDocument()
  })

  it('makes room when a folded group is unfolded and saves the shifted positions', async () => {
    shotGraph(5)  // 默认全折叠
    renderPage()
    await screen.findByLabelText('展开镜头 1')
    fireEvent.click(screen.getByLabelText('展开镜头 1'))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    const [, graph] = api.save.mock.calls[api.save.mock.calls.length - 1]
    const y = (id: string) => graph.nodes!.find((item) => item.id === id)!.position.y
    expect(y('k1')).toBe(0)
    expect(y('k2')).toBeGreaterThanOrEqual(500 + 24 + 48)  // 镜头一展开后的底边加间距
  })

  it('corrects the room once unfolded members get their real (taller) size, and saves it', async () => {
    const RealObserver = globalThis.ResizeObserver
    const heights = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight')
    const widths = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth')
    // 节点一显示出来就被“测量”成 280 x 700（折叠时的隐藏节点测量不到，只有展开后才有）
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get() { return (this as HTMLElement).classList.contains('react-flow__node') ? 700 : 0 } })
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get() { return (this as HTMLElement).classList.contains('react-flow__node') ? 280 : 0 } })
    globalThis.ResizeObserver = class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) { window.setTimeout(() => this.callback([{ target, contentRect: { width: 800, height: 600 } } as ResizeObserverEntry], this as never), 0) }
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
    try {
      shotGraph(5)  // 默认全折叠
      renderPage()
      await screen.findByLabelText('展开镜头 1')
      await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 50)) })
      fireEvent.click(screen.getByLabelText('展开镜头 1'))
      await screen.findByLabelText('镜头 1（已展开）')
      await waitFor(() => {
        const [, graph] = api.save.mock.calls[api.save.mock.calls.length - 1] || []
        const y = (id: string) => graph?.nodes?.find((item) => item.id === id)?.position.y ?? 0
        // 镜头一成员实测 700 高：组框底边 = 700 + 24，镜头二小卡片顶边（k2.y - 68）至少在它之下 48
        expect(y('k2') - 68).toBeGreaterThanOrEqual(700 + 24 + 48)
      }, { timeout: 4000 })
    } finally {
      globalThis.ResizeObserver = RealObserver
      if (heights) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', heights)
      if (widths) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', widths)
    }
  })

  it('deletes the edges of a visible node that is deleted, even when the other end is folded away', async () => {
    api.get.mockResolvedValue({
      id: 'c1', title: '雨夜', aspect_ratio: '9:16', node_count: 3, updated_at: null, created_at: null, revision: 3,
      graph: {
        nodes: [
          { id: 'k', type: 'image', position: { x: 0, y: 0 }, data: { label: '共用首帧', prompt: 'p' } },
          { id: 'v1', type: 'video', position: { x: 400, y: 0 }, data: { label: '1 · 甲', order: 0 } },
          { id: 'v2', type: 'video', position: { x: 400, y: 1200 }, data: { label: '2 · 乙', order: 1 } },
        ],
        edges: [
          { id: 'e1', source: 'k', target: 'v1', sourceHandle: 'out', targetHandle: 'first_frame' },
          { id: 'e2', source: 'k', target: 'v2', sourceHandle: 'out', targetHandle: 'first_frame' },
        ],
        viewport: { x: 0, y: 0, zoom: 1 },
      },
    })
    renderPage('/?node=k')
    await screen.findByLabelText('镜头 1（已展开）')
    await waitFor(() => expect(screen.getByDisplayValue('共用首帧').closest('.nc-node')).toHaveClass('nc-node--selected'))
    fireEvent.click(screen.getByRole('button', { name: '全部折叠' }))
    fireEvent.keyDown(document.body, { key: 'Delete', code: 'Delete' })
    fireEvent.keyUp(document.body, { key: 'Delete', code: 'Delete' })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })
    const [, graph] = api.save.mock.calls[api.save.mock.calls.length - 1]
    expect(graph.nodes?.map((item) => item.id).sort()).toEqual(['v1', 'v2'])  // 隐藏的视频节点没被删
    expect(graph.edges).toEqual([])  // 连到隐藏节点的边跟着被删节点一起走
  })

  it('opening with ?node= on an already unfolded, non-overlapping group saves nothing', async () => {
    shotGraph(2, {}, 3000)
    const { container } = renderPage('/?node=k1')
    await screen.findByLabelText('镜头 1（已展开）')
    await waitFor(() => expect(container.querySelector('.nc-node--selected')).not.toBeNull())
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 1200)) })
    expect(api.save).not.toHaveBeenCalled()
  })

  it('opening a folded group by ?node= unfolds it without shoving the others by guessed sizes', async () => {
    shotGraph(5, {}, 300)  // 5 个镜头默认全折叠，卡片靠得很近：若按默认尺寸让位就会推开后面的
    renderPage('/?node=k3')
    expect(await screen.findByDisplayValue('画面3')).toBeInTheDocument()
    expect(screen.getByLabelText('镜头 3（已展开）')).toBeInTheDocument()
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 1200)) })
    expect(api.save).not.toHaveBeenCalled()
  })

  describe('menus', () => {
    it('moves focus into the menu, walks it with arrows, and gives focus back on Escape / after a choice', async () => {
      renderPage()
      await screen.findByDisplayValue('雨夜便利店')
      const trigger = screen.getByRole('button', { name: '更多' })
      trigger.focus()
      fireEvent.click(trigger)
      const items = screen.getAllByRole('menuitem')
      expect(items[0]).toHaveFocus()
      fireEvent.keyDown(items[0], { key: 'ArrowDown' })
      expect(items[1]).toHaveFocus()
      fireEvent.keyDown(items[1], { key: 'ArrowUp' })
      fireEvent.keyDown(items[0], { key: 'ArrowUp' })  // 到头回绕到最后一个可用项（整理布局在没有节点时禁用不算）
      const usable = screen.getAllByRole('menuitem').filter((item) => !(item as HTMLButtonElement).disabled)
      expect(document.activeElement).toBe(usable[usable.length - 1])
      fireEvent.keyDown(document.activeElement as Element, { key: 'Escape' })
      expect(screen.queryByRole('menuitem')).not.toBeInTheDocument()
      expect(trigger).toHaveFocus()

      fireEvent.click(trigger)
      fireEvent.click(screen.getByRole('menuitem', { name: '新建文本节点' }))
      expect(screen.queryByRole('menuitem')).not.toBeInTheDocument()
      expect(trigger).toHaveFocus()
    })

    it('closes when clicking outside', async () => {
      renderPage()
      await screen.findByDisplayValue('雨夜便利店')
      fireEvent.click(screen.getByRole('button', { name: '分段生成' }))
      expect(screen.getAllByRole('menuitem').length).toBeGreaterThan(0)
      fireEvent.mouseDown(document.body)
      expect(screen.queryByRole('menuitem')).not.toBeInTheDocument()
    })
  })

  it('re-corrects when a re-shown group is measured taller than before it was folded', async () => {
    const RealObserver = globalThis.ResizeObserver
    const heights = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight')
    const widths = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth')
    let nodeHeight = 700
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get() { return (this as HTMLElement).classList.contains('react-flow__node') ? nodeHeight : 0 } })
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get() { return (this as HTMLElement).classList.contains('react-flow__node') ? 280 : 0 } })
    globalThis.ResizeObserver = class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) { window.setTimeout(() => this.callback([{ target, contentRect: { width: 800, height: 600 } } as ResizeObserverEntry], this as never), 0) }
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
    const lastY = (id: string) => {
      const [, graph] = api.save.mock.calls[api.save.mock.calls.length - 1] || []
      return graph?.nodes?.find((item) => item.id === id)?.position.y ?? 0
    }
    try {
      shotGraph(5)
      renderPage()
      await screen.findByLabelText('展开镜头 1')
      fireEvent.click(screen.getByLabelText('展开镜头 1'))
      await waitFor(() => expect(lastY('k2') - 68).toBeGreaterThanOrEqual(700 + 24 + 48), { timeout: 4000 })
      fireEvent.click(screen.getByLabelText('折叠镜头 1'))
      nodeHeight = 900  // 折叠期间节点变高了（比如结果历史变多）
      fireEvent.click(await screen.findByLabelText('展开镜头 1'))
      await waitFor(() => expect(lastY('k2') - 68).toBeGreaterThanOrEqual(900 + 24 + 48), { timeout: 4000 })
    } finally {
      globalThis.ResizeObserver = RealObserver
      if (heights) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', heights)
      if (widths) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', widths)
    }
  })
})

describe('NodeCanvasPage audio node', () => {
  function withAudio(extra: { data?: Record<string, unknown>; runs?: Parameters<typeof api.runs.mockResolvedValue>[0] } = {}) {
    api.get.mockResolvedValue({
      id: 'c1', title: '雨夜', aspect_ratio: '9:16', node_count: 2, updated_at: null, created_at: null, revision: 3,
      graph: {
        nodes: [
          { id: 'char-1', type: 'image', position: { x: 0, y: 0 }, data: { label: '角色 · 狐妖', role: 'character', character: '狐妖', voice: 'Cherry' } },
          { id: 'audio-1', type: 'audio', position: { x: 400, y: 0 }, data: { label: '台词 · 狐妖', speaker: '狐妖', text: '你终于来了', ...extra.data } },
        ],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      },
    })
    if (extra.runs) api.runs.mockResolvedValue(extra.runs)
    api.quote.mockResolvedValue({ kind: 'tts', model: 'qwen_tts', characters: 5, estimated_cost: 0.0004 })
  }

  it('creates an audio node from the More menu', async () => {
    renderPage()
    await screen.findByDisplayValue('雨夜便利店')
    chooseMenu('更多', '新建配音节点')
    expect(await screen.findByLabelText('说话人')).toBeInTheDocument()
    expect(screen.getByLabelText('台词')).toBeInTheDocument()
    expect(screen.getByLabelText('音色')).toBeInTheDocument()
    expect(screen.getByText('写台词，或连入一个文本节点')).toBeInTheDocument()
  })

  it('edits speaker, line and voice, shows the price and submits a tts request with the speaker voice', async () => {
    withAudio()
    renderPage()
    const line = await screen.findByLabelText('台词')
    expect(screen.getByLabelText('说话人')).toHaveValue('狐妖')
    expect(await screen.findByText('芊悦 · 阳光积极')).toBeInTheDocument()
    expect(await screen.findByText('预计 ¥0.000')).toBeInTheDocument()
    fireEvent.change(line, { target: { value: '你终于来了。' } })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), { timeout: 3000 })  // 等自动保存落地再点生成
    const run = within(line.closest('.nc-node') as HTMLElement).getByText('生成').closest('button')!
    await waitFor(() => expect(run).not.toBeDisabled())
    await act(async () => { fireEvent.click(run) })
    await waitFor(() => expect(api.run).toHaveBeenCalled())
    expect(api.run.mock.calls[0][1]).toBe('audio-1')
    expect(api.run.mock.calls[0][2]).toMatchObject({ kind: 'tts', text: '你终于来了。', voice: 'Cherry' })
  })

  it('plays the selected result and shows its duration, with a result picker for several', async () => {
    const result = (id: string, ms: number) => ({ artifact_id: id, type: 'audio', url: `/api/artifacts/${id}/download`, duration_ms: ms })
    withAudio({ runs: { nodes: { 'audio-1': { jobs: [], results: [result('r2', 3200), result('r1', 2800)] } }, active_jobs: 0, spent: 0 } })
    renderPage()
    expect(await screen.findByTestId('line-audio')).toHaveAttribute('src', '/api/artifacts/r2/download')
    expect(screen.getByText('3.2 秒')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('选用结果'), { target: { value: 'r1' } })
    expect(await screen.findByText('2.8 秒')).toBeInTheDocument()
    expect(screen.getByTestId('line-audio')).toHaveAttribute('src', '/api/artifacts/r1/download')
  })
})


describe('NodeCanvasPage video reference images', () => {
  const withReferences = () => {
    api.get.mockResolvedValue({
      id: 'c1', title: '雨夜', aspect_ratio: '9:16', node_count: 4, updated_at: null, created_at: null, revision: 3,
      graph: {
        nodes: [
          { id: 'c-a', type: 'image', position: { x: 0, y: 0 }, data: { label: '角色 · 狐妖', role: 'character', character: '狐妖' } },
          { id: 'c-b', type: 'image', position: { x: 0, y: 600 }, data: { label: '角色 · 书生', role: 'character', character: '书生' } },
          { id: 'v1', type: 'video', position: { x: 600, y: 0 }, data: { label: '对视', prompt: '两人对视', duration: 5, tier: 'preview' } },
        ],
        edges: [
          { id: 'e1', source: 'c-a', target: 'v1', sourceHandle: 'out', targetHandle: 'reference' },
          { id: 'e2', source: 'c-b', target: 'v1', sourceHandle: 'out', targetHandle: 'reference' },
        ],
        viewport: { x: 0, y: 0, zoom: 1 },
      },
    })
    const done = (id: string) => ({ jobs: [], results: [{ artifact_id: id, type: 'image', url: `/api/artifacts/${id}/download` }] })
    api.runs.mockResolvedValue({ nodes: { 'c-a': done('ra'), 'c-b': done('rb') }, active_jobs: 0, spent: 0 })
  }

  it('shows how many character images the video refers to, with the price from the quote', async () => {
    withReferences()
    api.quote.mockResolvedValue({ kind: 'video', model: 'seedance-2-mini', mode: 'reference', estimated_cost: 0.93 })
    renderPage()
    expect(await screen.findByText('参考 2 张角色图')).toBeInTheDocument()
    expect(await screen.findByText('预计 ¥0.930')).toBeInTheDocument()
    const request = api.quote.mock.calls[api.quote.mock.calls.length - 1][1]
    expect(request).toMatchObject({ kind: 'video', reference_artifact_ids: ['ra', 'rb'], first_frame_artifact_id: null })
    // 视频节点多了一个「参考图」输入口
    expect(screen.getAllByText('参考图').length).toBeGreaterThan(0)
  })

  it('blocks generating when a first frame is also connected', async () => {
    withReferences()
    const loaded = await api.get('c1')
    loaded.graph.nodes!.push({ id: 'k', type: 'image', position: { x: 300, y: 0 }, data: { label: '关键帧' } })
    loaded.graph.edges!.push({ id: 'e3', source: 'k', target: 'v1', sourceHandle: 'out', targetHandle: 'first_frame' })
    renderPage()
    expect(await screen.findByText('参考图和首帧不能同时使用：去掉首帧连线，或去掉参考图连线')).toBeInTheDocument()
  })
})

describe('NodeCanvasPage narrow toolbar and thumbnails', () => {
  const shots = () => {
    api.get.mockResolvedValue({
      id: 'c1', title: '雨夜', aspect_ratio: '9:16', node_count: 2, updated_at: null, created_at: null, revision: 3,
      graph: {
        nodes: [
          { id: 'k1', type: 'image', position: { x: 0, y: 0 }, data: { label: '1 · 开场 关键帧', prompt: '画面1', shot_size: '近景' } },
          { id: 'v1', type: 'video', position: { x: 400, y: 0 }, data: { label: '1 · 开场', prompt: '视频1', duration: 4, order: 0 } },
        ] as never,
        edges: [{ id: 'e1', source: 'k1', target: 'v1', sourceHandle: 'out', targetHandle: 'first_frame' }] as never,
        viewport: { x: 0, y: 0, zoom: 1 },
      },
    })
  }
  afterEach(() => { vi.unstubAllGlobals() })

  const stubWidth = (narrow: boolean) => vi.stubGlobal('matchMedia', (query: string) => ({
    matches: narrow, media: query, addEventListener: () => undefined, removeEventListener: () => undefined,
  }))

  it('keeps fold-all and add-shot on the toolbar on wide screens', async () => {
    shots(); stubWidth(false)
    renderPage()
    await screen.findByLabelText('镜头 1（已展开）')
    expect(screen.getByRole('button', { name: '全部折叠' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '添加镜头' })).toBeInTheDocument()
  })

  it('moves fold-all and add-shot into the More menu below 900px', async () => {
    shots(); stubWidth(true)
    renderPage()
    await screen.findByLabelText('镜头 1（已展开）')
    expect(screen.queryByRole('button', { name: '全部折叠' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '添加镜头' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '更多' }))
    expect(screen.getByRole('menuitem', { name: '添加镜头' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem', { name: '全部折叠' }))
    expect(screen.getByLabelText('展开镜头 1')).toBeInTheDocument()
  })

  it('sizes the collapsed shot thumbnail by the canvas aspect ratio', async () => {
    shots(); stubWidth(false)
    const { container, unmount } = renderPage()
    fireEvent.click(await screen.findByLabelText('折叠镜头 1'))
    const portrait = container.querySelector('.nc-group-card__thumb') as HTMLElement
    expect(portrait.style.aspectRatio).toBe('9 / 16')
    expect(portrait.style.height).toBe('48px')
    unmount()

    window.localStorage.clear()
    shots()
    api.get.mockResolvedValue({ ...(await api.get('c1')), aspect_ratio: '16:9' })
    const wide = renderPage()
    fireEvent.click(await screen.findByLabelText('折叠镜头 1'))
    const landscape = wide.container.querySelector('.nc-group-card__thumb') as HTMLElement
    expect(landscape.style.aspectRatio).toBe('16 / 9')
    expect(landscape.style.width).toBe('64px')
  })

  it('falls back to portrait for an unknown ratio and treats 1:1 as square', () => {
    expect(thumbStyle('weird')).toEqual({ aspectRatio: '9 / 16', height: 48 })
    expect(thumbStyle('1:1')).toEqual({ aspectRatio: '1 / 1', width: 64 })
  })
})
