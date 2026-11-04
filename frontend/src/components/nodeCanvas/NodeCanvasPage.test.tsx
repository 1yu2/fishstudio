import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nodeCanvasApi } from '../../api/nodeCanvas'
import NodeCanvasPage from './NodeCanvasPage'

vi.mock('../../api/nodeCanvas', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/nodeCanvas')>()
  return {
    ...actual,
    nodeCanvasApi: {
      get: vi.fn(), models: vi.fn(), runs: vi.fn(), save: vi.fn(), quote: vi.fn(), run: vi.fn(), upload: vi.fn(), split: vi.fn(), rename: vi.fn(), cancelJob: vi.fn(),
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

  it('explains a missing image model before generating', async () => {
    api.models.mockResolvedValue({ image: [], video: [], defaults: { image: '', video_preview: '', video_final: '' }, image_problem: '还没有可用的图片模型：去设置添加' })
    renderPage()
    await screen.findByDisplayValue('雨夜便利店')
    fireEvent.click(screen.getByRole('button', { name: '图片' }))
    fireEvent.change(await screen.findByLabelText('提示词'), { target: { value: '女店员' } })
    expect(await screen.findByText('还没有可用的图片模型：去设置添加')).toBeInTheDocument()
    expect((await screen.findByText('生成')).closest('button')).toBeDisabled()
    expect(screen.getByLabelText('上传')).not.toBeDisabled()
  })
})

