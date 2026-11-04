import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { forwardRef } from 'react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { finalApi, RenderBlockedError, type FinalState, type RenderInfo } from '../../api/final'
import { ConflictError, nodeCanvasApi, type StoredNode } from '../../api/nodeCanvas'
import { ConfirmHost } from '../ui/ConfirmDialog'
import { toast } from '../ui/Toast'
import FinalPage from './FinalPage'

vi.mock('../../api/nodeCanvas', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/nodeCanvas')>()
  return { ...actual, nodeCanvasApi: { get: vi.fn(), save: vi.fn(), saveKeepalive: vi.fn() } }
})
vi.mock('../../api/final', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/final')>()
  return { ...actual, finalApi: { get: vi.fn(), ensureNode: vi.fn(), render: vi.fn(), uploadBgm: vi.fn(), download: vi.fn() } }
})
vi.mock('../ui/Toast', () => ({ toast: vi.fn() }))
vi.mock('../common/ProtectedMedia', () => ({
  ProtectedImage: ({ src }: { src: string }) => <img src={src} alt="" />,
  ProtectedVideo: forwardRef<HTMLVideoElement, { src: string }>(({ src }, ref) => <video ref={ref} src={src} data-testid="player" />),
}))

const nc = vi.mocked(nodeCanvasApi)
const fa = vi.mocked(finalApi)
const setFooter = vi.fn()
const refreshSummary = vi.fn()

const episodeNode = (data: Record<string, unknown> = {}): StoredNode => ({ id: 'ep', type: 'episode', position: { x: 900, y: 0 }, data: { label: '成片 · 第 1 集', ...data } })
const videoNode = (): StoredNode => ({ id: 'v1', type: 'video', position: { x: 400, y: 0 }, data: {} })

const render1 = (extra: Partial<RenderInfo> = {}): RenderInfo => ({
  job_id: 'j1', status: 'succeeded', progress: 100, error: '', artifact_id: 'a1', url: '/api/artifacts/a1/download', srt_url: '/api/artifacts/s1/download',
  input_hash: 'h1', created_at: null, ...extra,
})

const finalState = (extra: Partial<FinalState> = {}): FinalState => ({
  episode_node_id: 'ep',
  settings: { burn_subtitles: true, export_srt: true, bgm_artifact_id: null, bgm_volume_db: -18, ducking: true, fades: true, resolution: '720p' },
  timeline: {
    shots: [
      { index: 0, shot_id: 'v1', title: '开场', start_ms: 0, end_ms: 6000, has_video: true, still: false, thumbnail_url: '/api/artifacts/t1/download' },
      { index: 1, shot_id: 'v2', title: '对视', start_ms: 6000, end_ms: 8000, has_video: true, still: false, thumbnail_url: null },
    ],
    voices: [{ node_id: 'a1', shot_index: 0, speaker: '狐妖', kind: 'dialogue', text: '你终于来了。', start_ms: 0, end_ms: 2000 }],
    subtitles: [{ start_ms: 0, end_ms: 2000, text: '你终于来了。' }],
    bgm: null,
  },
  total_ms: 8000, problems: [], warnings: [], input_hash: 'h1', latest_render: null, last_success: null, stale: false,
  ...extra,
})

function setup(state: FinalState = finalState(), nodes: StoredNode[] = [videoNode(), episodeNode()]) {
  nc.get.mockResolvedValue({ id: 'c1', title: 't', aspect_ratio: '9:16', node_count: nodes.length, updated_at: null, created_at: null, revision: 3, graph: { nodes, edges: [] } })
  nc.save.mockResolvedValue({ revision: 4 })
  fa.get.mockResolvedValue(state)
  fa.render.mockResolvedValue({ job_id: 'j2', status: 'pending' })
  fa.download.mockResolvedValue(undefined)
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/studio/c1/final']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <ConfirmHost />
      <Routes>
        <Route element={<Outlet context={{ summary: null, loading: false, refreshSummary, setFooter }} />}>
          <Route path="/studio/:canvasId/final" element={<FinalPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

const lastFooter = () => {
  const items = setFooter.mock.calls.map((call) => call[0]).filter(Boolean)
  return items[items.length - 1]
}

beforeEach(() => {
  vi.clearAllMocks()
  setup()
})

describe('FinalPage load', () => {
  it('creates the episode node when the canvas has none, then loads the page', async () => {
    fa.get.mockResolvedValueOnce(finalState({ episode_node_id: null })).mockResolvedValue(finalState())
    fa.ensureNode.mockResolvedValue({ episode_node_id: 'ep', revision: 4 })
    renderPage()
    expect(await screen.findByLabelText('成片设置')).toBeInTheDocument()
    expect(fa.ensureNode).toHaveBeenCalledWith('c1', 3)
  })

  it('does not call ensure-node when the node already exists', async () => {
    renderPage()
    await screen.findByLabelText('成片设置')
    expect(fa.ensureNode).not.toHaveBeenCalled()
  })

  it('shows the placeholder when nothing was rendered and keeps AIGC / QC out of the UI as coming soon', async () => {
    renderPage()
    expect(await screen.findByText('还没有渲染过')).toBeInTheDocument()
    expect(screen.getByText(/自动加入“AI 生成”标识（即将上线）/)).toBeInTheDocument()
    expect(screen.getByText('出片检查（即将上线）')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /AI 生成/ })).toBeDisabled()
  })

  it('plays the latest successful render', async () => {
    setup(finalState({ latest_render: render1(), last_success: render1() }))
    renderPage()
    expect((await screen.findByTestId('player')).getAttribute('src')).toBe('/api/artifacts/a1/download')
  })
})

describe('FinalPage settings write-back', () => {
  it('saves a changed setting into the episode node data with the revision', async () => {
    renderPage()
    fireEvent.click(await screen.findByLabelText('烧录到画面'))
    await waitFor(() => expect(nc.save).toHaveBeenCalled(), { timeout: 3000 })
    const [id, graph, revision] = nc.save.mock.calls[0]
    expect(id).toBe('c1')
    expect(revision).toBe(3)
    expect(graph.nodes!.find((n) => n.id === 'ep')!.data).toMatchObject({ burn_subtitles: false, label: '成片 · 第 1 集' })
    expect(graph.nodes!.find((n) => n.id === 'v1')!.data).toEqual({})
  })

  it('reloads and reports on a 409', async () => {
    nc.save.mockRejectedValueOnce(new ConflictError('画布已更新'))
    renderPage()
    fireEvent.click(await screen.findByLabelText('同时导出 SRT 文件'))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('画布在别处被修改，已刷新', 'error'), { timeout: 3000 })
    await waitFor(() => expect(nc.get.mock.calls.length).toBeGreaterThanOrEqual(2))
  })
})

describe('FinalPage timeline', () => {
  it('draws blocks in proportion to time and a playhead that follows clicks', async () => {
    renderPage()
    const lane = (await screen.findByRole('group', { name: '画面轨道' }))
    const blocks = lane.querySelectorAll<HTMLElement>('.fn-block')
    expect(blocks).toHaveLength(2)
    expect(blocks[0].style.left).toBe('0%')
    expect(blocks[0].style.width).toBe('75%')
    expect(blocks[1].style.left).toBe('75%')
    expect(blocks[1].style.width).toBe('25%')
    expect(within(screen.getByRole('group', { name: '配音轨道' })).getByText('狐妖')).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: '字幕轨道' })).getByText('你终于来了。')).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'BGM轨道' })).getByText('没有背景音乐')).toBeInTheDocument()
    expect(screen.getByText('时间线（只读，调整顺序和入出点请回到分镜）')).toBeInTheDocument()
    expect(screen.getByText(/0:08 · 8 秒/)).toBeInTheDocument()

    lane.getBoundingClientRect = () => ({ left: 100, width: 400, top: 0, right: 500, bottom: 40, height: 40, x: 100, y: 0, toJSON: () => ({}) })
    fireEvent.click(lane, { clientX: 300 })  // 正中间
    expect(screen.getByTestId('playhead').getAttribute('data-fraction')).toBe('0.50000')
  })

  it('shows the bgm block with its name', async () => {
    setup(finalState({ settings: { ...finalState().settings, bgm_artifact_id: 'b1' }, timeline: { ...finalState().timeline, bgm: { artifact_id: 'b1', name: '雪夜·古风钢琴', start_ms: 0, end_ms: 8000 } } }))
    renderPage()
    const lane = await screen.findByRole('group', { name: 'BGM轨道' })
    expect(within(lane).getByText('雪夜·古风钢琴')).toBeInTheDocument()
    expect(lane.querySelector<HTMLElement>('.fn-block')!.style.width).toBe('100%')
  })
})

describe('FinalPage problems and render', () => {
  it('disables rendering while there are problems and links to the storyboard', async () => {
    setup(finalState({ problems: [{ code: 'missing_video', message: '镜头 2 还没有视频', shot_index: 1, shot_id: 'v2' }] }))
    renderPage()
    expect(await screen.findByText('镜头 2 还没有视频')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '渲染预览' })).toBeDisabled()
    expect(screen.getByText('先解决上面的问题再渲染')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '去分镜' }).getAttribute('href')).toBe('/studio/c1/storyboard')
    expect(lastFooter().hint).toBe('镜头 2 还没有视频')
  })

  it('links overlong dubbing warnings to the voice step without blocking render', async () => {
    setup(finalState({ warnings: [{ code: 'voice_overflow', message: '镜头 1 的配音比镜头长', shot_index: 0 }] }))
    renderPage()
    expect(await screen.findByText('镜头 1 的配音比镜头长')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '去配音' }).getAttribute('href')).toBe('/studio/c1/voice')
    expect(screen.getByRole('button', { name: '渲染预览' })).toBeEnabled()
  })

  it('submits a render with the revision and a client request id', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '渲染预览' }))
    await waitFor(() => expect(fa.render).toHaveBeenCalled())
    const [id, body] = fa.render.mock.calls[0]
    expect(id).toBe('c1')
    expect(body.base_revision).toBe(3)
    expect(body.client_request_id).toEqual(expect.any(String))
  })

  it('shows progress while rendering and disables the button', async () => {
    setup(finalState({ latest_render: render1({ status: 'running', progress: 40, artifact_id: null, url: null, srt_url: null }) }))
    renderPage()
    const bar = await screen.findByRole('progressbar', { name: '渲染进度' })
    expect(bar.getAttribute('aria-valuenow')).toBe('40')
    expect(screen.getByText('渲染中 40%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /重新渲染预览|渲染预览/ })).toBeDisabled()
  })

  it('shows the failure reason and retries', async () => {
    setup(finalState({ latest_render: render1({ status: 'failed', error: '镜头 3 的视频文件不存在', artifact_id: null, url: null, srt_url: null }) }))
    renderPage()
    expect(await screen.findByText('渲染失败：镜头 3 的视频文件不存在')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试渲染' }))
    await waitFor(() => expect(fa.render).toHaveBeenCalledTimes(1))
  })

  it('shows blocked-by-server messages when the render request is refused', async () => {
    fa.render.mockRejectedValueOnce(new RenderBlockedError('镜头 2 还没有视频', [{ code: 'missing_video', message: '镜头 2 还没有视频', shot_index: 1 }]))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '渲染预览' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('镜头 2 还没有视频')
  })

  it('shows the stale notice', async () => {
    setup(finalState({ stale: true, latest_render: render1({ input_hash: 'old' }), last_success: render1({ input_hash: 'old' }) }))
    renderPage()
    expect(await screen.findByRole('status')).toHaveTextContent('素材已变更，需要重新渲染')
    expect(lastFooter().hint).toBe('素材已变更，需要重新渲染')
  })
})

describe('FinalPage export', () => {
  it('disables export until a render exists, in the page and in the footer', async () => {
    renderPage()
    expect(await screen.findByRole('button', { name: '导出成片' })).toBeDisabled()
    expect(lastFooter()).toMatchObject({ label: '导出成片', disabled: true, note: '先渲染一次' })
  })

  it('downloads the latest render and the srt', async () => {
    setup(finalState({ latest_render: render1(), last_success: render1() }))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '导出成片' }))
    await waitFor(() => expect(fa.download).toHaveBeenCalledWith('/api/artifacts/a1/download', expect.stringMatching(/\.mp4$/)))
    fireEvent.click(screen.getByRole('button', { name: '下载 SRT 字幕' }))
    await waitFor(() => expect(fa.download).toHaveBeenCalledWith('/api/artifacts/s1/download', expect.stringMatching(/\.srt$/)))
    expect(lastFooter()).toMatchObject({ disabled: false })
  })

  it('asks before downloading a stale render', async () => {
    setup(finalState({ stale: true, latest_render: render1(), last_success: render1() }))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '导出成片' }))
    expect(await screen.findByText('当前成片不是最新素材，仍要下载吗？')).toBeInTheDocument()
    expect(fa.download).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await act(async () => undefined)
    expect(fa.download).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '导出成片' }))
    fireEvent.click(await screen.findByRole('button', { name: '仍要下载' }))
    await waitFor(() => expect(fa.download).toHaveBeenCalledTimes(1))
  })
})

describe('FinalPage leaving and export freshness', () => {
  it('confirms on beforeunload and sends a keepalive save on pagehide while settings are unsaved', async () => {
    renderPage()
    fireEvent.click(await screen.findByLabelText('烧录到画面'))
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    window.dispatchEvent(new Event('pagehide'))
    expect(nc.saveKeepalive).toHaveBeenCalledTimes(1)
    const [id, graph, revision] = nc.saveKeepalive.mock.calls[0]
    expect(id).toBe('c1')
    expect(revision).toBe(3)
    expect(graph.nodes!.find((n) => n.id === 'ep')!.data).toMatchObject({ burn_subtitles: false })
  })

  it('does not block leaving when nothing is unsaved', async () => {
    renderPage()
    await screen.findByLabelText('成片设置')
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    window.dispatchEvent(new Event('pagehide'))
    expect(unload.defaultPrevented).toBe(false)
    expect(nc.saveKeepalive).not.toHaveBeenCalled()
  })

  it('re-fetches the final state on export and uses the fresh stale flag', async () => {
    // 页面上看起来是最新的，点击导出时服务端状态已经过期
    setup(finalState({ stale: false, latest_render: render1(), last_success: render1() }))
    renderPage()
    const button = await screen.findByRole('button', { name: '导出成片' })
    fa.get.mockResolvedValue(finalState({ stale: true, latest_render: render1(), last_success: render1() }))
    fireEvent.click(button)
    expect(await screen.findByText('当前成片不是最新素材，仍要下载吗？')).toBeInTheDocument()
    expect(fa.download).not.toHaveBeenCalled()
  })

  it('waits for unsaved settings to be saved before checking freshness', async () => {
    setup(finalState({ latest_render: render1(), last_success: render1() }))
    renderPage()
    fireEvent.click(await screen.findByLabelText('同时导出 SRT 文件'))
    fireEvent.click(screen.getByRole('button', { name: '导出成片' }))
    await waitFor(() => expect(fa.download).toHaveBeenCalled(), { timeout: 3000 })
    expect(nc.save).toHaveBeenCalled()
    expect(nc.save.mock.invocationCallOrder[0]).toBeLessThan(fa.download.mock.invocationCallOrder[0])
  })
})

describe('FinalPage background music', () => {
  it('uploads a file, writes the artifact id into the node and shows its name; removing clears it', async () => {
    fa.uploadBgm.mockResolvedValue({ artifact_id: 'b9', name: '雪夜.mp3', duration_ms: 90000 })
    renderPage()
    await screen.findByLabelText('成片设置')
    // 保存之后服务端返回的新设置
    fa.get.mockResolvedValue(finalState({
      settings: { ...finalState().settings, bgm_artifact_id: 'b9' },
      timeline: { ...finalState().timeline, bgm: { artifact_id: 'b9', name: '雪夜.mp3', start_ms: 0, end_ms: 8000 } },
    }))
    const input = await screen.findByLabelText('选择背景音乐文件')
    fireEvent.change(input, { target: { files: [new File(['x'], '雪夜.mp3', { type: 'audio/mpeg' })] } })
    expect(await screen.findByText('雪夜.mp3')).toBeInTheDocument()
    expect(fa.uploadBgm).toHaveBeenCalledWith('c1', expect.any(File))
    await waitFor(() => expect(nc.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(nc.save.mock.calls[0][1].nodes!.find((n) => n.id === 'ep')!.data).toMatchObject({ bgm_artifact_id: 'b9' })

    fa.get.mockResolvedValue(finalState())
    fireEvent.click(await screen.findByRole('button', { name: '移除' }))
    expect(screen.getByText('没有背景音乐', { selector: '.fn-bgm__name' })).toBeInTheDocument()
    await waitFor(() => expect(nc.save).toHaveBeenCalledTimes(2), { timeout: 3000 })
    expect(nc.save.mock.calls[1][1].nodes!.find((n) => n.id === 'ep')!.data).toMatchObject({ bgm_artifact_id: null })
  })

  it('writes the volume slider (-40..0) and the ducking / fades switches', async () => {
    setup(finalState({ settings: { ...finalState().settings, bgm_artifact_id: 'b1' } }), [videoNode(), episodeNode({ bgm_artifact_id: 'b1' })])
    renderPage()
    const slider = await screen.findByLabelText('背景音乐音量')
    expect(slider.getAttribute('min')).toBe('-40')
    expect(slider.getAttribute('max')).toBe('0')
    fireEvent.change(slider, { target: { value: '-30' } })
    fireEvent.click(screen.getByLabelText('有人声时自动压低音乐'))
    fireEvent.click(screen.getByLabelText('开头结尾淡入淡出'))
    fireEvent.change(screen.getByLabelText('清晰度'), { target: { value: '1080p' } })
    await waitFor(() => expect(nc.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(nc.save.mock.calls[nc.save.mock.calls.length - 1][1].nodes!.find((n) => n.id === 'ep')!.data).toMatchObject({
      bgm_volume_db: -30, ducking: false, fades: false, resolution: '1080p', bgm_artifact_id: 'b1',
    })
  })
})
