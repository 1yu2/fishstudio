import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nodeCanvasApi, type BatchQuote, type CanvasRuns, type PresetVoice, type StoredEdge, type StoredNode } from '../../api/nodeCanvas'
import { toast } from '../ui/Toast'
import VoicePage from './VoicePage'
import { resetVoiceCache } from './useVoiceList'

vi.mock('../../api/nodeCanvas', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/nodeCanvas')>()
  return {
    ...actual,
    nodeCanvasApi: {
      get: vi.fn(), models: vi.fn(), voices: vi.fn(), runs: vi.fn(), save: vi.fn(), saveKeepalive: vi.fn(), quote: vi.fn(), run: vi.fn(),
      batchQuote: vi.fn(), batchRun: vi.fn(), batchList: vi.fn(), batchGet: vi.fn(),
    },
  }
})
vi.mock('../ui/Toast', () => ({ toast: vi.fn() }))
vi.mock('../common/ProtectedMedia', () => ({
  ProtectedImage: ({ src }: { src: string }) => <img src={src} alt="" />,
  ProtectedAudio: ({ src }: { src: string }) => <audio data-testid="player" src={src} />,
}))

const api = vi.mocked(nodeCanvasApi)

const node = (id: string, type: StoredNode['type'], data: Record<string, unknown>, y = 0): StoredNode => ({ id, type, position: { x: 0, y }, data })
const link = (source: string, target: string, targetHandle: string): StoredEdge => ({ id: `e-${source}-${target}`, source, target, sourceHandle: 'out', targetHandle })
const job = (status: string, extra: Record<string, unknown> = {}) => ({ id: 'j', status, progress: 0, queue_position: 0, error: '', model: '', created_at: null, ...extra })
const audio = (artifact_id: string, duration_ms: number) => ({ artifact_id, type: 'audio', url: `/api/artifacts/${artifact_id}/download`, duration_ms })

const VOICES: PresetVoice[] = [
  { id: 'Cherry', name: '芊悦', gender: 'female', style: '阳光积极', sample_text: '你好', sample_url: '/samples/cherry.mp3' },
  { id: 'Moon', name: '月白', gender: 'male', style: '率性帅气', sample_text: '你好', sample_url: null },
]

// 狐妖已选音色、书生没选；旁白的两句没选音色
const baseNodes = (): StoredNode[] => [
  node('fox', 'image', { role: 'character', character: '狐妖', voice: 'Cherry' }, -400),
  node('scholar', 'image', { role: 'character', character: '书生' }, -300),
  node('v1', 'video', { label: '1 · 开场', duration: 4, order: 0 }, 0),
  node('v2', 'video', { label: '2 · 对视', duration: 4, order: 1 }, 400),
  node('v3', 'video', { label: '3 · 回忆', duration: 5, order: 2 }, 800),
  node('a1', 'audio', { speaker: '狐妖', text: '你终于来了。', order: 0 }, 0),
  node('a2', 'audio', { speaker: '狐妖', text: '三百年前你救过一只白狐。', order: 0 }, 400),
  node('a3', 'audio', { speaker: '旁白', text: '那一夜，雪落无声。', order: 0 }, 800),
  node('a4', 'audio', { speaker: '旁白', text: '天亮了。', order: 1 }, 900),
  node('a5', 'audio', { speaker: '书生', text: '姑娘认错人了吧？', voice: 'Moon', order: 1 }, 410),
]
const baseEdges = (): StoredEdge[] => [
  link('a1', 'v1', 'voice'), link('a2', 'v2', 'voice'), link('a3', 'v3', 'voice'), link('a4', 'v3', 'voice'), link('a5', 'v2', 'voice'),
]
const baseRuns = (): CanvasRuns => ({
  nodes: {
    a1: { jobs: [job('succeeded')], results: [audio('r1', 2800)] },
    a2: { jobs: [job('succeeded')], results: [audio('r2', 5200)] },  // 比 4 秒镜头长 1.2 秒
  },
  active_jobs: 0,
  spent: 0,
})

const quoteOf = (total: number, runnable: number): BatchQuote => ({
  revision: 3, nodes: [], estimated_total: total, billable_total: total, runnable_count: runnable, waiting_count: 0, blocking_count: 0,
  unavailable_nodes: [], budget: { cap: null, remaining: null }, exceeds_budget: false,
})

function setupApi(overrides: { nodes?: StoredNode[]; edges?: StoredEdge[]; runs?: CanvasRuns } = {}) {
  api.get.mockResolvedValue({
    id: 'c1', title: '雪夜狐妖', aspect_ratio: '9:16', node_count: 8, updated_at: null, created_at: null, revision: 3,
    graph: { nodes: overrides.nodes ?? baseNodes(), edges: overrides.edges ?? baseEdges() },
  })
  api.voices.mockResolvedValue(VOICES)
  api.models.mockResolvedValue({ image: [], video: [], defaults: { image: '', video_preview: '', video_final: '' } })
  api.runs.mockResolvedValue(overrides.runs ?? baseRuns())
  api.save.mockResolvedValue({ revision: 4 })
  api.quote.mockResolvedValue({ kind: 'tts', model: 'qwen_tts', characters: 6, estimated_cost: 0.012 })
  api.run.mockResolvedValue({ job_id: 'j1', status: 'pending' })
  api.batchList.mockResolvedValue([])
  api.batchQuote.mockResolvedValue(quoteOf(0.024, 2))
  api.batchRun.mockResolvedValue({
    id: 'b1', status: 'running', kinds: ['tts'], node_ids: [], nodes: {}, total: 2, completed: 0, failed: 0, estimated_total: 0.024,
  })
}

function renderPage(setFooter = vi.fn()) {
  const context = { summary: null, loading: false, refreshSummary: vi.fn(), setFooter }
  const view = render(
    <MemoryRouter initialEntries={['/studio/c1/voice']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/studio/:canvasId" element={<Outlet context={context} />}>
          <Route path="voice" element={<VoicePage />} />
          <Route path="final" element={<h1>FINAL</h1>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
  return { ...view, setFooter }
}

const lastSave = () => api.save.mock.calls[api.save.mock.calls.length - 1]
const savedData = (id: string) => lastSave()[1].nodes!.find((item) => item.id === id)!.data
const lastFooter = (setFooter: ReturnType<typeof vi.fn>) => setFooter.mock.calls.filter((call) => call[0]).pop()?.[0]
const row = (text: string) => screen.getByText(text).closest('li') as HTMLElement
const SLOW = { timeout: 3000 }

beforeEach(() => {
  vi.clearAllMocks()
  resetVoiceCache()
  setupApi()
})

describe('VoicePage speakers', () => {
  it('lists the speakers that have lines (characters first, narrator last) with their voice', async () => {
    renderPage()
    const side = await screen.findByRole('complementary', { name: '角色音色' })
    expect(await within(side).findByLabelText('狐妖的音色')).toHaveValue('Cherry')
    expect(within(side).getByLabelText('书生的音色')).toHaveValue('')
    expect(within(side).getByLabelText('旁白的音色')).toHaveValue('')
    const names = within(side).getAllByRole('combobox').map((item) => item.getAttribute('aria-label'))
    expect(names).toEqual(['狐妖的音色', '书生的音色', '旁白的音色'])
    expect(within(side).getAllByText('未选择')).toHaveLength(2)
    expect(within(side).getAllByRole('option', { name: '选择一个音色' })).toHaveLength(3)
  })

  it('only shows the preview button for voices with a sample', async () => {
    renderPage()
    await screen.findByLabelText('狐妖的音色')
    expect(screen.getByRole('button', { name: /试听/ })).toBeInTheDocument()  // 狐妖的 Cherry 有示例音频
    fireEvent.change(screen.getByLabelText('书生的音色'), { target: { value: 'Moon' } })  // Moon 没有示例音频
    expect(screen.getAllByRole('button', { name: /试听/ })).toHaveLength(1)
  })

  it('writes a character voice to the base look node', async () => {
    renderPage()
    fireEvent.change(await screen.findByLabelText('书生的音色'), { target: { value: 'Moon' } })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), SLOW)
    expect(savedData('scholar').voice).toBe('Moon')
  })

  it('writes the narrator voice to every narrator line', async () => {
    renderPage()
    fireEvent.change(await screen.findByLabelText('旁白的音色'), { target: { value: 'Cherry' } })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), SLOW)
    expect([savedData('a3').voice, savedData('a4').voice]).toEqual(['Cherry', 'Cherry'])
    expect(savedData('a1').voice).toBeUndefined()  // 其他说话人的句子不动
  })
})

describe('VoicePage lines', () => {
  it('shows lines by shot with their states', async () => {
    api.runs.mockResolvedValue({
      nodes: {
        ...baseRuns().nodes,
        a3: { jobs: [job('running', { progress: 40 })], results: [] },
        a4: { jobs: [job('failed', { error: '额度不足' })], results: [] },
      },
      active_jobs: 1, spent: 0,
    })
    renderPage()
    expect(await screen.findByText('按镜头的台词 · 5 句')).toBeInTheDocument()
    const done = row('你终于来了。')
    expect(within(done).getByText('镜头 1 · 狐妖')).toBeInTheDocument()
    expect(within(done).getByText('2.8 秒')).toBeInTheDocument()
    expect(within(done).getByText('已生成')).toBeInTheDocument()
    expect(within(done).getByRole('button', { name: '播放' })).toBeInTheDocument()
    expect(within(row('那一夜，雪落无声。')).getByText('生成中 40%')).toBeInTheDocument()
    const failed = row('天亮了。')
    expect(within(failed).getByText('额度不足')).toBeInTheDocument()
    expect(within(failed).getByRole('button', { name: /重试/ })).toBeInTheDocument()
  })

  it('says a line still needs a voice, and offers a priced generate button otherwise', async () => {
    renderPage()
    expect(within(await screen.findByText('那一夜，雪落无声。').then((el) => el.closest('li') as HTMLElement)).getByText('待选音色')).toBeInTheDocument()
    const todo = row('姑娘认错人了吧？')
    expect(await within(todo).findByRole('button', { name: '生成 · ¥0.012' })).toBeEnabled()
    fireEvent.click(within(todo).getByRole('button', { name: /生成/ }))
    await waitFor(() => expect(api.run).toHaveBeenCalled())
    expect(api.run.mock.calls[0][1]).toBe('a5')
    expect(api.run.mock.calls[0][2]).toMatchObject({ kind: 'tts', text: '姑娘认错人了吧？', voice: 'Moon' })
  })

  it('plays a generated line on demand', async () => {
    renderPage()
    const done = await screen.findByText('你终于来了。').then((el) => el.closest('li') as HTMLElement)
    expect(within(done).queryByTestId('player')).not.toBeInTheDocument()
    fireEvent.click(within(done).getByRole('button', { name: '播放' }))
    expect(within(done).getByTestId('player')).toHaveAttribute('src', '/api/artifacts/r1/download')
  })
})

describe('VoicePage overrun', () => {
  it('warns when dubbing is longer than the shot video and offers two ways out', async () => {
    renderPage()
    const long = await screen.findByText('三百年前你救过一只白狐。').then((el) => el.closest('li') as HTMLElement)
    expect(within(long).getByText('配音 5.2 秒，比镜头长 1.2 秒')).toBeInTheDocument()
    expect(within(long).getByRole('button', { name: '延长镜头' })).toBeInTheDocument()
    expect(within(long).getByRole('button', { name: '缩短台词' })).toBeInTheDocument()
    // 没超出的不提示
    expect(screen.queryAllByText(/比镜头长/)).toHaveLength(1)
  })

  it('extends the shot video to cover the dubbing and says the video must be regenerated', async () => {
    renderPage()
    const long = await screen.findByText('三百年前你救过一只白狐。').then((el) => el.closest('li') as HTMLElement)
    fireEvent.click(within(long).getByRole('button', { name: '延长镜头' }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), SLOW)
    expect(savedData('v2').duration).toBe(6)
    expect(toast).toHaveBeenCalledWith('镜头已延长到 6 秒，需要重新生成这个镜头的视频', 'success')
    expect(screen.queryByText(/比镜头长/)).not.toBeInTheDocument()
    expect(screen.getByText('镜头已延长到 6 秒，需要重新生成视频')).toBeInTheDocument()
  })

  it('refuses to extend past the longest shot', async () => {
    setupApi({ runs: { nodes: { a2: { jobs: [job('succeeded')], results: [audio('r2', 13000)] } }, active_jobs: 0, spent: 0 } })
    renderPage()
    const long = await screen.findByText('三百年前你救过一只白狐。').then((el) => el.closest('li') as HTMLElement)
    fireEvent.click(within(long).getByRole('button', { name: '延长镜头' }))
    expect(toast).toHaveBeenCalledWith('这个视频模型最长的镜头也放不下这段配音，请缩短台词', 'error')
    expect(api.save).not.toHaveBeenCalled()
  })

  it('opens the line for editing and focuses the input when shortening', async () => {
    renderPage()
    const long = await screen.findByText('三百年前你救过一只白狐。').then((el) => el.closest('li') as HTMLElement)
    fireEvent.click(within(long).getByRole('button', { name: '缩短台词' }))
    const input = within(long).getByLabelText('镜头 2 台词')
    expect(input).toHaveFocus()
    fireEvent.change(input, { target: { value: '你救过一只白狐。' } })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), SLOW)
    expect(savedData('a2').text).toBe('你救过一只白狐。')
    // 改过后可以重新生成
    expect(await within(long).findByRole('button', { name: '重新生成 · ¥0.012' })).toBeEnabled()
    fireEvent.click(within(long).getByRole('button', { name: '完成' }))
    expect(within(long).queryByLabelText('镜头 2 台词')).not.toBeInTheDocument()
  })
})

describe('VoicePage generate remaining', () => {
  // 所有句子都选好音色、没有生成中 / 空台词，才能批量生成
  const ready = () => setupApi({
    nodes: baseNodes().map((item) => item.id === 'scholar' ? { ...item, data: { ...item.data, voice: 'Moon' } }
      : item.id === 'a3' || item.id === 'a4' ? { ...item, data: { ...item.data, voice: 'Cherry' } } : item),
  })

  it('shows the remaining count and estimate, and runs only tts nodes after confirming the quote', async () => {
    ready()
    renderPage()
    const button = await screen.findByRole('button', { name: '生成剩余 3 句 · 预估 ¥0.024' })
    expect(button).toBeEnabled()
    expect(api.batchQuote).toHaveBeenCalledWith('c1', { scope: 'all', kinds: ['tts'] })
    fireEvent.click(button)
    fireEvent.click(await screen.findByRole('button', { name: '确认运行' }))
    await waitFor(() => expect(api.batchRun).toHaveBeenCalled())
    expect(api.batchRun.mock.calls[0][1]).toMatchObject({ scope: 'all', kinds: ['tts'], skip_succeeded: true, quoted_revision: 3 })
  })

  it('is disabled and says why while a line has no voice', async () => {
    renderPage()
    const button = await screen.findByRole('button', { name: /生成剩余/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', '先给旁白选一个音色')
    expect(screen.getByRole('note')).toHaveTextContent('先给旁白选一个音色')
  })

  it('is disabled while a line is generating, to avoid submitting it twice', async () => {
    ready()
    api.runs.mockResolvedValue({ nodes: { ...baseRuns().nodes, a5: { jobs: [job('running')], results: [] } }, active_jobs: 1, spent: 0 })
    renderPage()
    const button = await screen.findByRole('button', { name: /生成剩余/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', '有台词正在生成')
  })
})

describe('VoicePage footer', () => {
  it('blocks going to the final cut until every line is dubbed, and names the silent speakers', async () => {
    const { setFooter, unmount } = renderPage()
    await screen.findByText('按镜头的台词 · 5 句')
    await waitFor(() => expect(lastFooter(setFooter)).toMatchObject({
      label: '配音完成，去成片 →', disabled: true, note: '还差 3 句台词没配音', hint: '书生、旁白还没有音色',
    }))
    unmount()
    expect(setFooter).toHaveBeenLastCalledWith(null)
  })

  it('enables the footer once all lines are dubbed and goes to the final cut', async () => {
    const done = (id: string, ms: number) => ({ jobs: [job('succeeded')], results: [audio(`r-${id}`, ms)] })
    setupApi({
      nodes: baseNodes().map((item) => item.id === 'scholar' ? { ...item, data: { ...item.data, voice: 'Moon' } } : item),
      runs: { nodes: { a1: done('a1', 2000), a2: done('a2', 3000), a3: done('a3', 2000), a4: done('a4', 1000), a5: done('a5', 1500) }, active_jobs: 0, spent: 0 },
    })
    const { setFooter } = renderPage()
    await screen.findByText('按镜头的台词 · 5 句')
    await waitFor(() => expect(lastFooter(setFooter)).toMatchObject({ disabled: false }))
    expect(lastFooter(setFooter).note).toBeUndefined()
    lastFooter(setFooter).onClick()
    expect(await screen.findByRole('heading', { name: 'FINAL' })).toBeInTheDocument()
  })

  it('lets you go straight to the final cut when there are no lines at all', async () => {
    setupApi({ nodes: baseNodes().filter((item) => item.type !== 'audio'), edges: [] })
    const { setFooter } = renderPage()
    expect(await screen.findByText('还没有台词')).toBeInTheDocument()
    expect(screen.getByText('按镜头的台词 · 0 句')).toBeInTheDocument()
    await waitFor(() => expect(lastFooter(setFooter)).toMatchObject({ disabled: false }))
    expect(screen.queryByRole('button', { name: /生成剩余/ })).not.toBeInTheDocument()
  })
})


describe('VoicePage review fixes', () => {
  it('limits "extend shot" to the duration range of the selected video model', async () => {
    api.models.mockResolvedValue({
      image: [],
      video: [{ slug: 'slow', name: 'Slow', model: 'm', provider: 'p', modes: [], pricing: {}, min_duration: 5, max_duration: 5 }],
      defaults: { image: '', video_preview: 'slow', video_final: 'slow' },
    })
    renderPage()
    const long = await screen.findByText('三百年前你救过一只白狐。').then((el) => el.closest('li') as HTMLElement)
    await waitFor(() => expect(api.models).toHaveBeenCalled())
    fireEvent.click(within(long).getByRole('button', { name: '延长镜头' }))  // 配音 5.2 秒，模型最长 5 秒
    expect(toast).toHaveBeenCalledWith('这个视频模型最长的镜头也放不下这段配音，请缩短台词', 'error')
    expect(api.save).not.toHaveBeenCalled()
  })

  it('treats 4.04 s of dubbing as longer than a 4 s shot', async () => {
    setupApi({ runs: { nodes: { a2: { jobs: [job('succeeded')], results: [audio('r2', 4040)] } }, active_jobs: 0, spent: 0 } })
    renderPage()
    expect(await screen.findByText('配音 4 秒，比镜头长 0.1 秒')).toBeInTheDocument()
  })

  it('uses the linked text node as the line and edits that node', async () => {
    const nodes = [...baseNodes(), node('src', 'text', { text: '来自文本节点的台词' }, -500)]
    nodes.find((item) => item.id === 'a1')!.data.text = ''  // 自己没写，台词全靠连入的文本
    setupApi({ nodes, edges: [...baseEdges(), link('src', 'a1', 'prompt')], runs: { nodes: {}, active_jobs: 0, spent: 0 } })
    renderPage()
    const li = await screen.findByText('来自文本节点的台词').then((el) => el.closest('li') as HTMLElement)
    expect(within(li).getByText(/来自文本节点/, { selector: 'span.vc-muted' })).toBeInTheDocument()
    expect(within(li).queryByText('台词为空')).not.toBeInTheDocument()
    expect(within(li).getByRole('button', { name: /生成/ })).toBeInTheDocument()
  })

  it('writes edits of a text-linked line back to the text node', async () => {
    const nodes = [...baseNodes(), node('src', 'text', { text: '连入的台词' }, -500)]
    setupApi({
      nodes, edges: [...baseEdges(), link('src', 'a2', 'prompt')],
      runs: { nodes: { a2: { jobs: [job('succeeded')], results: [audio('r2', 5200)] } }, active_jobs: 0, spent: 0 },
    })
    renderPage()
    const li = await screen.findByText('连入的台词').then((el) => el.closest('li') as HTMLElement)
    fireEvent.click(within(li).getByRole('button', { name: '缩短台词' }))
    fireEvent.change(within(li).getByLabelText('镜头 2 台词'), { target: { value: '短一点' } })
    await waitFor(() => expect(api.save).toHaveBeenCalled(), SLOW)
    expect(savedData('src').text).toBe('短一点')
    expect(savedData('a2').text).toBe('三百年前你救过一只白狐。')  // 节点自己的 text 不动
  })

  describe('lines outside any shot', () => {
    const loose = () => setupApi({
      nodes: [...baseNodes(), node('loose', 'audio', { speaker: '旁白', text: '没人认领的台词', voice: 'Cherry' }, 900), node('blank', 'audio', { speaker: '旁白', text: '' }, 950)],
      runs: { nodes: {}, active_jobs: 0, spent: 0 },
    })

    it('lists them in their own group, counts them, and ignores blank nodes for the count and footer', async () => {
      loose()
      const { setFooter } = renderPage()
      const group = await screen.findByRole('region', { name: '未归属镜头的台词' })
      expect(within(group).getByText('未归属镜头的台词 · 1 句')).toBeInTheDocument()
      expect(within(group).getByText('没人认领的台词')).toBeInTheDocument()
      expect(within(group).getByText('台词为空')).toBeInTheDocument()
      expect(screen.getByText('按镜头的台词 · 6 句')).toBeInTheDocument()  // 5 句 + loose，blank 不算
      await waitFor(() => expect(lastFooter(setFooter)).toMatchObject({ note: '还差 6 句台词没配音' }))
    })

    it('assigns a line to a shot with a voice edge and can delete one', async () => {
      loose()
      renderPage()
      const group = await screen.findByRole('region', { name: '未归属镜头的台词' })
      fireEvent.change(within(group).getByLabelText('把「没人认领的台词」归到镜头'), { target: { value: 'v1' } })
      await waitFor(() => expect(api.save).toHaveBeenCalled(), SLOW)
      expect(lastSave()[1].edges!.some((item) => item.source === 'loose' && item.target === 'v1' && item.targetHandle === 'voice')).toBe(true)
    })

    it('deletes a loose line together with its edges', async () => {
      loose()
      renderPage()
      fireEvent.click(await screen.findByRole('button', { name: '删除「没人认领的台词」' }))
      await waitFor(() => expect(api.save).toHaveBeenCalled(), SLOW)
      expect(lastSave()[1].nodes!.some((item) => item.id === 'loose')).toBe(false)
    })
  })

  it('locks submission outside the panel: closing it mid-flight cannot start a second batch', async () => {
    let finish!: (value: unknown) => void
    api.batchRun.mockImplementation(() => new Promise((resolve) => { finish = resolve }) as never)
    setupApi({ nodes: baseNodes().map((item) => item.id === 'scholar' ? { ...item, data: { ...item.data, voice: 'Moon' } } : item.id === 'a3' || item.id === 'a4' ? { ...item, data: { ...item.data, voice: 'Cherry' } } : item) })
    api.batchRun.mockImplementation(() => new Promise((resolve) => { finish = resolve }) as never)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /生成剩余 3 句/ }))
    const confirm = await screen.findByRole('button', { name: '确认运行' })
    fireEvent.click(confirm)
    fireEvent.click(confirm)  // 连点也只提交一次
    await waitFor(() => expect(api.batchRun).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: '确认运行' })).toBeDisabled()
    expect(screen.getByText('正在提交，请稍候…')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '关闭批量运行' }))
    const again = screen.getByRole('button', { name: /生成剩余/ })
    expect(again).toBeDisabled()
    expect(again).toHaveAttribute('title', '正在提交')
    const quotes = api.batchQuote.mock.calls.length
    fireEvent.click(again)
    expect(api.batchQuote).toHaveBeenCalledTimes(quotes)  // 提交中再点也不会再报价 / 开新批次
    finish({ id: 'b1', status: 'running', kinds: ['tts'], node_ids: [], nodes: {}, total: 3, completed: 0, failed: 0, estimated_total: 0 })
    await waitFor(() => expect(again).not.toHaveAttribute('title', '正在提交'))
    expect(api.batchRun).toHaveBeenCalledTimes(1)
  })

  it('does not let blank nodes block the batch button', async () => {
    setupApi({
      nodes: [...baseNodes().map((item) => item.id === 'scholar' ? { ...item, data: { ...item.data, voice: 'Moon' } } : item.id === 'a3' || item.id === 'a4' ? { ...item, data: { ...item.data, voice: 'Cherry' } } : item), node('blank', 'audio', { speaker: '旁白', text: '' }, 950)],
    })
    renderPage()
    expect(await screen.findByRole('button', { name: /生成剩余 3 句/ })).toBeEnabled()
  })
})
