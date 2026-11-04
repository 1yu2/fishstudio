import { render, screen, waitFor } from '@testing-library/react'
import type { NodeProps } from '@xyflow/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { finalApi, type FinalState } from '../../api/final'
import { CanvasContext, type CanvasActions } from './context'
import { buildGroups } from './groups'
import { EpisodeNode } from './nodes'

vi.mock('../../api/final', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/final')>()
  return { ...actual, finalApi: { get: vi.fn() } }
})
const fa = vi.mocked(finalApi)

const state = (extra: Partial<FinalState> = {}): FinalState => ({
  episode_node_id: 'ep',
  settings: { burn_subtitles: true, export_srt: true, bgm_artifact_id: null, bgm_volume_db: -18, ducking: true, fades: true, resolution: '720p' },
  timeline: {
    shots: [1, 2, 3].map((n) => ({ index: n - 1, shot_id: `v${n}`, title: `镜头${n}`, start_ms: 0, end_ms: 1, has_video: true, still: false, thumbnail_url: null })),
    voices: [
      { node_id: 'a1', shot_index: 0, speaker: '狐妖', kind: 'dialogue', text: '你好', start_ms: 0, end_ms: 1 },
      { node_id: 'a2', shot_index: 1, speaker: '旁白', kind: 'narration', text: '雪', start_ms: 0, end_ms: 1 },
    ],
    subtitles: [], bgm: { artifact_id: 'b', name: '钢琴', start_ms: 0, end_ms: 1 },
  },
  total_ms: 3, problems: [], warnings: [], input_hash: 'h', latest_render: null, last_success: null, stale: false,
  ...extra,
})

function renderNode(nodes: unknown[] = [], edges: unknown[] = []) {
  const actions = { canvasId: 'c1', runs: null, nodes, edges } as unknown as CanvasActions
  const props = { id: 'ep', data: { label: '成片 · 第 1 集' }, selected: false } as unknown as NodeProps
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <CanvasContext.Provider value={actions}><EpisodeNode {...props} /></CanvasContext.Provider>
    </MemoryRouter>,
  )
}

beforeEach(() => { vi.clearAllMocks() })

describe('EpisodeNode', () => {
  it('summarizes shots, dubbing lines and bgm, and links to the final page', async () => {
    fa.get.mockResolvedValue(state())
    renderNode()
    expect(screen.getByText('成片 · 第 1 集')).toBeInTheDocument()
    expect(await screen.findByText('按镜头顺序收集 3 个镜头 · 2 句配音 · BGM', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.queryByText('素材已变更，需要重新渲染')).toBeNull()
    expect(screen.getByRole('link', { name: '去成片' }).getAttribute('href')).toBe('/studio/c1/final')
  })

  it('warns when the assets changed after the last render', async () => {
    fa.get.mockResolvedValue(state({ stale: true }))
    renderNode()
    expect(await screen.findByText('素材已变更，需要重新渲染', {}, { timeout: 3000 })).toBeInTheDocument()
  })

  it('requests the summary once, not on every render', async () => {
    fa.get.mockResolvedValue(state())
    const view = renderNode()
    await waitFor(() => expect(fa.get).toHaveBeenCalledTimes(1), { timeout: 3000 })
    view.rerender(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><div /></MemoryRouter>)
    expect(fa.get).toHaveBeenCalledTimes(1)
  })
})

describe('EpisodeNode refresh', () => {
  it('refreshes the summary after the shots change in the graph', async () => {
    fa.get.mockResolvedValue(state())
    const video = (order: number) => ({ id: 'v1', type: 'video', position: { x: 0, y: 0 }, data: { order } })
    const view = renderNode([video(0)])
    await waitFor(() => expect(fa.get).toHaveBeenCalledTimes(1), { timeout: 3000 })
    const props = { id: 'ep', data: { label: '成片 · 第 1 集' }, selected: false } as unknown as NodeProps
    const again = (nodes: unknown[]) => view.rerender(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <CanvasContext.Provider value={{ canvasId: 'c1', runs: null, nodes, edges: [] } as unknown as CanvasActions}><EpisodeNode {...props} /></CanvasContext.Provider>
      </MemoryRouter>,
    )
    again([video(0)])  // 同样的镜头：不重新请求
    again([video(1), { id: 'a2', type: 'audio', position: { x: 0, y: 0 }, data: {} }])
    await waitFor(() => expect(fa.get).toHaveBeenCalledTimes(2), { timeout: 4000 })
  })
})

describe('episode node in shot groups', () => {
  it('is never part of a shot group', () => {
    const nodes = [
      { id: 'k1', type: 'image' as const, position: { x: 0, y: 0 }, data: {} },
      { id: 'v1', type: 'video' as const, position: { x: 400, y: 0 }, data: {} },
      { id: 'ep', type: 'episode' as const, position: { x: 900, y: 0 }, data: {} },
    ]
    const edges = [{ id: 'e', source: 'k1', target: 'v1', targetHandle: 'first_frame' }]
    const groups = buildGroups(nodes, edges, null)
    expect(groups.flatMap((group) => group.members)).not.toContain('ep')
  })
})
