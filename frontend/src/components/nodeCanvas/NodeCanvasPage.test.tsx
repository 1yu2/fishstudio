import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConflictError, nodeCanvasApi } from '../../api/nodeCanvas'
import NodeCanvasPage from './NodeCanvasPage'

vi.mock('../../api/nodeCanvas', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/nodeCanvas')>()
  return {
    ...actual,
    nodeCanvasApi: {
      get: vi.fn(), models: vi.fn(), runs: vi.fn(), save: vi.fn(), quote: vi.fn(), run: vi.fn(), upload: vi.fn(), split: vi.fn(), rename: vi.fn(), cancelJob: vi.fn(),
      batchQuote: vi.fn(), batchRun: vi.fn(), batchList: vi.fn(), batchGet: vi.fn(), batchCancel: vi.fn(),
    },
  }
})
vi.mock('../ui/Toast', () => ({ toast: vi.fn() }))

beforeAll(() => {
  // React Flow 在 jsdom 里需要这些浏览器 API；未测量尺寸的节点是 visibility:hidden，按 label / 文本查询
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver
  // @ts-expect-error jsdom 没有 DOMMatrixReadOnly
  globalThis.DOMMatrixReadOnly = class { m22 = 1; constructor() {} }
})

const api = vi.mocked(nodeCanvasApi)

beforeEach(() => {
  vi.clearAllMocks()
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

function renderPage() {
  return render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><NodeCanvasPage canvasId="c1" /></MemoryRouter>)
}

describe('NodeCanvasPage', () => {
  it('loads the graph and shows spend', async () => {
    renderPage()
    expect(await screen.findByDisplayValue('雨夜便利店')).toBeInTheDocument()
    expect(screen.getByDisplayValue('雨夜')).toBeInTheDocument()
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
    fireEvent.click(screen.getByRole('button', { name: '图片' }))
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
    estimated_total: 0.2, billable_total: 0.2, unavailable_nodes: [{ node_id: 'vid-1', label: '推门', reason: '未配置价格' }],
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
})
