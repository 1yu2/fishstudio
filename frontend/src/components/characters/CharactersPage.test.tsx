import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { libraryApi, type LibraryCharacter } from '../../api/library'
import { ConflictError, nodeCanvasApi, type CanvasRuns, type StoredNode } from '../../api/nodeCanvas'
import { toast } from '../ui/Toast'
import CharactersPage from './CharactersPage'

vi.mock('../../api/nodeCanvas', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/nodeCanvas')>()
  return {
    ...actual,
    nodeCanvasApi: { get: vi.fn(), models: vi.fn(), runs: vi.fn(), save: vi.fn(), saveKeepalive: vi.fn(), quote: vi.fn(), run: vi.fn() },
  }
})
vi.mock('../../api/library', () => ({
  libraryApi: { list: vi.fn(), upload: vi.fn(), update: vi.fn(), remove: vi.fn(), saveToLibrary: vi.fn(), addToProject: vi.fn() },
}))
vi.mock('../ui/Toast', () => ({ toast: vi.fn() }))
vi.mock('../common/ProtectedMedia', () => ({
  ProtectedImage: ({ src, alt }: { src: string; alt?: string }) => <img src={src} alt={alt ?? ''} />,
}))

const api = vi.mocked(nodeCanvasApi)
const lib = vi.mocked(libraryApi)

const node = (id: string, data: Record<string, unknown>, y = 0): StoredNode => ({ id, type: 'image', position: { x: 0, y }, data })
const character = (id: string, name: string, extra: Record<string, unknown> = {}, y = 0) =>
  node(id, { label: `角色 · ${name}`, role: 'character', character: name, prompt: `${name}的外貌`, model: '', count: 4, ...extra }, y)
const result = (artifact_id: string) => ({ artifact_id, type: 'image', url: `/api/artifacts/${artifact_id}/download` })
const job = (status: string, progress = 0, error = '') => ({ id: 'j', status, progress, queue_position: 0, error, model: '', created_at: null })

const graphNodes = (): StoredNode[] => [
  character('fox', '狐妖', { selected: 'f2', library_asset_id: 'lib-fox' }, 0),
  character('scholar', '书生', {}, 380),
  character('monk', '老和尚', {}, 760),
]
const runsValue = (): CanvasRuns => ({
  nodes: {
    fox: { jobs: [], results: [result('f2'), result('f1')] },
    // 最新在前：s5..s2 是最新一批，s1 是更早的
    scholar: { jobs: [], results: ['s5', 's4', 's3', 's2', 's1'].map(result) },
    monk: { jobs: [job('running', 40)], results: [] },
  },
  active_jobs: 1,
  spent: 0,
})
const libraryItems = (): LibraryCharacter[] => [
  { id: 'lib-fox', name: '狐妖', description: '白发', looks: [{ name: '默认', artifact_id: 'x1', url: '/api/artifacts/x1/download' }], cover_url: '/api/artifacts/x1/download', updated_at: null, in_project: true },
  { id: 'lib-ding', name: '阿丁', description: '神灯少年', looks: [{ name: '默认', artifact_id: 'x2', url: '/api/artifacts/x2/download' }, { name: '冬装', artifact_id: 'x3', url: '/api/artifacts/x3/download' }], cover_url: '/api/artifacts/x2/download', updated_at: null, in_project: false },
  { id: 'lib-zhi', name: '织女', description: '鹊桥', looks: [{ name: '默认', artifact_id: 'x4', url: '/api/artifacts/x4/download' }], cover_url: '/api/artifacts/x4/download', updated_at: null, in_project: false },
]

function setupApi(overrides: { nodes?: StoredNode[]; runs?: CanvasRuns } = {}) {
  api.get.mockResolvedValue({
    id: 'c1', title: '雪夜狐妖', aspect_ratio: '9:16', node_count: 3, updated_at: null, created_at: null, revision: 3,
    graph: { nodes: overrides.nodes ?? graphNodes(), edges: [] },
  })
  api.models.mockResolvedValue({ image: [], video: [], defaults: { image: 'seedream', video_preview: 'h', video_final: 'h' }, image_problem: '' })
  api.runs.mockResolvedValue(overrides.runs ?? runsValue())
  api.save.mockResolvedValue({ revision: 4 })
  api.quote.mockResolvedValue({ kind: 'image', model: 'seedream', estimated_cost: 0.12 })
  api.run.mockResolvedValue({ job_id: 'j1', status: 'pending' })
  lib.list.mockResolvedValue(libraryItems())
  lib.addToProject.mockResolvedValue({ revision: 4, node_ids: ['n1'] })
  lib.saveToLibrary.mockResolvedValue(libraryItems()[0])
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/studio/c1/characters']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes><Route path="/studio/:canvasId/characters" element={<CharactersPage />} /></Routes>
    </MemoryRouter>,
  )
}

const card = (name: string) => screen.getByRole('article', { name: `角色 ${name}` })
const lastSave = () => api.save.mock.calls[api.save.mock.calls.length - 1]
const savedNode = (id: string) => lastSave()[1].nodes!.find((item) => item.id === id)!
const SLOW = { timeout: 3000 }

beforeEach(() => {
  vi.clearAllMocks()
  setupApi()
})

describe('CharactersPage cards', () => {
  it('shows each character in its state: final, waiting for a pick, generating', async () => {
    renderPage()
    expect(await screen.findByText('本集角色 · 3')).toBeInTheDocument()
    expect(screen.getByText('定稿后会用于所有镜头，保证长相一致')).toBeInTheDocument()
    const fox = within(card('狐妖'))
    expect(fox.getByText('已定稿')).toBeInTheDocument()
    expect(fox.getByText('来自角色库')).toBeInTheDocument()
    expect(fox.getByRole('img', { name: '狐妖定稿图' })).toHaveAttribute('src', '/api/artifacts/f2/download')
    const scholar = within(card('书生'))
    expect(scholar.getByText('等你挑选')).toBeInTheDocument()
    expect(scholar.getAllByRole('button', { name: /^选择第 \d 张$/ })).toHaveLength(4)
    expect(scholar.getByRole('button', { name: '定为标准形象' })).toBeDisabled()
    const monk = within(card('老和尚'))
    expect(monk.getByText('正在生成 4 张设定图')).toBeInTheDocument()
    expect(monk.getByText('生成中 40%')).toBeInTheDocument()
  })

  it('picks a candidate and writes it back as the final image (selected)', async () => {
    renderPage()
    await screen.findByText('本集角色 · 3')
    const scholar = within(card('书生'))
    fireEvent.click(scholar.getByRole('button', { name: '选择第 2 张' }))
    fireEvent.click(scholar.getByRole('button', { name: '定为标准形象' }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), SLOW)
    expect(lastSave()[0]).toBe('c1')
    expect(lastSave()[2]).toBe(3)
    expect(savedNode('scholar').data.selected).toBe('s4')
    expect(await within(card('书生')).findByText('已定稿')).toBeInTheDocument()
  })

  it('expands earlier candidates beyond the latest batch of 4', async () => {
    renderPage()
    await screen.findByText('本集角色 · 3')
    const scholar = within(card('书生'))
    fireEvent.click(scholar.getByRole('button', { name: '更早的 1 张' }))
    expect(scholar.getAllByRole('button', { name: /^选择第 \d 张$/ })).toHaveLength(5)
  })

  it('regenerates 4 more with the quoted price and a count=4 request', async () => {
    renderPage()
    await screen.findByText('本集角色 · 3')
    const button = await within(card('书生')).findByRole('button', { name: '再生成 4 张 · ¥0.120' })
    expect(api.quote).toHaveBeenCalledWith('c1', expect.objectContaining({ kind: 'image', count: 4, prompt: '书生的外貌' }))
    fireEvent.click(button)
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1))
    const [canvasId, nodeId, body] = api.run.mock.calls[0]
    expect([canvasId, nodeId]).toEqual(['c1', 'scholar'])
    expect(body).toMatchObject({ kind: 'image', count: 4, aspect_ratio: '9:16', prompt: '书生的外貌' })
    expect(body.client_request_id).toBeTruthy()
  })

  it('keeps paid buttons disabled until a quote for the current request arrives', async () => {
    const resolvers: ((value: { kind: 'image'; model: string; estimated_cost: number }) => void)[] = []
    const price = { kind: 'image' as const, model: 'seedream', estimated_cost: 0.12 }
    let released = false
    api.quote.mockImplementation(() => released ? Promise.resolve(price) : new Promise((done) => { resolvers.push(done) }))
    renderPage()
    const scholar = within(await screen.findByRole('article', { name: '角色 书生' }))
    const pending = await scholar.findByRole('button', { name: '再生成 4 张 · 报价中…' })
    expect(pending).toBeDisabled()
    fireEvent.click(pending)
    expect(api.run).not.toHaveBeenCalled()
    released = true
    resolvers.forEach((resolve) => resolve(price))
    expect(await scholar.findByRole('button', { name: '再生成 4 张 · ¥0.120' })).toBeEnabled()
  })

  it('drops an old price when the request changes (rename description, new quote pending)', async () => {
    renderPage()
    const first = within(await screen.findByRole('article', { name: '角色 书生' }))
    await first.findByRole('button', { name: '再生成 4 张 · ¥0.120' })
    api.quote.mockImplementation(() => new Promise(() => undefined))
    fireEvent.click(first.getByRole('button', { name: '编辑' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('外貌描述'), { target: { value: '换了描述' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))
    const after = within(await screen.findByRole('article', { name: '角色 书生' }))
    const button = await after.findByRole('button', { name: '再生成 4 张 · 报价中…' })
    expect(button).toBeDisabled()
  })

  it('offers a first generation for a character without results, and a retry with the reason after a failure', async () => {
    setupApi({
      nodes: [character('a', '甲'), character('b', '乙')],
      runs: { nodes: { b: { jobs: [job('failed', 0, '供应商超时')], results: [] } }, active_jobs: 0, spent: 0 },
    })
    renderPage()
    expect(await within(await screen.findByRole('article', { name: '角色 甲' })).findByRole('button', { name: '生成 4 张 · ¥0.120' })).toBeEnabled()
    const failed = within(card('乙'))
    expect(failed.getByRole('alert')).toHaveTextContent('供应商超时')
    expect(await failed.findByRole('button', { name: '重试 · ¥0.120' })).toBeEnabled()
  })

  it('disables generation and says why when quoting fails or no model is available', async () => {
    api.quote.mockRejectedValue(new Error('没有可用的图片模型'))
    setupApi()
    api.quote.mockRejectedValue(new Error('没有可用的图片模型'))
    renderPage()
    const scholar = within(await screen.findByRole('article', { name: '角色 书生' }))
    await waitFor(() => expect(scholar.getByText('没有可用的图片模型')).toBeInTheDocument())
    expect(scholar.getByRole('button', { name: '再生成 4 张' })).toBeDisabled()

    api.models.mockResolvedValue({ image: [], video: [], defaults: { image: '', video_preview: '', video_final: '' }, image_problem: '还没有设置图片模型' })
    renderPage()
    expect((await screen.findAllByText('还没有设置图片模型')).length).toBeGreaterThan(0)
  })
})

describe('CharactersPage editing', () => {
  it('adds a look as a same-name character node and generates it after showing the price', async () => {
    renderPage()
    await screen.findByText('本集角色 · 3')
    fireEvent.click(within(card('狐妖')).getByRole('button', { name: '新增造型' }))
    const dialog = await screen.findByRole('dialog', { name: '狐妖 · 新造型' })
    fireEvent.change(within(dialog).getByLabelText('造型名'), { target: { value: '冬装' } })
    fireEvent.change(within(dialog).getByLabelText('和基础造型的差异'), { target: { value: '披黑色斗篷' } })
    const go = await within(dialog).findByRole('button', { name: '建好并生成 · ¥0.120' })
    fireEvent.click(go)
    await waitFor(() => expect(api.run).toHaveBeenCalledTimes(1))
    const [, nodeId, body] = api.run.mock.calls[0]
    expect(body).toMatchObject({ kind: 'image', count: 4, prompt: '狐妖的外貌，造型：披黑色斗篷' })
    const created = lastSave()[1].nodes!.find((item) => item.id === nodeId)!
    expect(created.data).toMatchObject({ role: 'character', character: '狐妖', look: '冬装', library_asset_id: 'lib-fox' })
    expect(within(card('狐妖')).getByRole('button', { name: '造型 冬装' })).toBeInTheDocument()
  })

  it('can create the look without generating', async () => {
    renderPage()
    await screen.findByText('本集角色 · 3')
    fireEvent.click(within(card('书生')).getByRole('button', { name: '新增造型' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('造型名'), { target: { value: '夜行' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '只建好' }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), SLOW)
    expect(api.run).not.toHaveBeenCalled()
    expect(lastSave()[1].nodes!.some((item) => item.data.look === '夜行' && item.data.character === '书生')).toBe(true)
  })

  it('rejects a duplicate look name', async () => {
    renderPage()
    await screen.findByText('本集角色 · 3')
    fireEvent.click(within(card('书生')).getByRole('button', { name: '新增造型' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('造型名'), { target: { value: '默认' } })
    expect(within(dialog).getByRole('alert')).toHaveTextContent('“默认”是基础造型')
    expect(within(dialog).getByRole('button', { name: '只建好' })).toBeDisabled()
  })

  it('renaming a character renames all of its look nodes (character and label)', async () => {
    setupApi({ nodes: [...graphNodes(), character('fox-w', '狐妖', { look: '冬装', label: '角色 · 狐妖 · 冬装' }, 1140)] })
    renderPage()
    await screen.findByText('本集角色 · 3')
    fireEvent.click(within(card('狐妖')).getByRole('button', { name: '编辑' }))
    const dialog = await screen.findByRole('dialog', { name: '编辑角色' })
    fireEvent.change(within(dialog).getByLabelText('角色名'), { target: { value: '九尾狐' } })
    fireEvent.change(within(dialog).getByLabelText('外貌描述'), { target: { value: '九条尾巴' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), SLOW)
    expect(savedNode('fox').data).toMatchObject({ character: '九尾狐', label: '角色 · 九尾狐', prompt: '九条尾巴' })
    expect(savedNode('fox-w').data).toMatchObject({ character: '九尾狐', label: '角色 · 九尾狐 · 冬装' })
    expect(savedNode('scholar').data.character).toBe('书生')
    expect(screen.getByRole('article', { name: '角色 九尾狐' })).toBeInTheDocument()
  })

  it('refuses a name that another character already uses', async () => {
    renderPage()
    await screen.findByText('本集角色 · 3')
    fireEvent.click(within(card('狐妖')).getByRole('button', { name: '编辑' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('角色名'), { target: { value: '书生' } })
    expect(within(dialog).getByRole('alert')).toHaveTextContent('已有同名角色')
    expect(within(dialog).getByRole('button', { name: '保存' })).toBeDisabled()
  })

  it('saves a finalized character to the library and says so; unfinalized ones cannot be saved', async () => {
    renderPage()
    await screen.findByText('本集角色 · 3')
    expect(within(card('书生')).getByRole('button', { name: '存入角色库' })).toBeDisabled()
    fireEvent.click(within(card('狐妖')).getByRole('button', { name: '存入角色库' }))
    await waitFor(() => expect(lib.saveToLibrary).toHaveBeenCalledWith('c1', '狐妖'))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('已存入角色库', 'success'))
    await waitFor(() => expect(lib.list.mock.calls.length).toBeGreaterThan(1))  // 角色库面板刷新
  })

  it('empty state explains and creates a character node', async () => {
    setupApi({ nodes: [], runs: { nodes: {}, active_jobs: 0, spent: 0 } })
    renderPage()
    expect(await screen.findByText('角色通常在剧本确认后自动识别；也可以手动添加，或从右侧角色库加入。')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '新建角色' }))
    const dialog = await screen.findByRole('dialog', { name: '新建角色' })
    fireEvent.change(within(dialog).getByLabelText('角色名'), { target: { value: '小狐' } })
    fireEvent.change(within(dialog).getByLabelText('外貌描述'), { target: { value: '红色围巾' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '新建' }))
    await waitFor(() => expect(api.save).toHaveBeenCalled(), SLOW)
    expect(lastSave()[1].nodes![0].data).toMatchObject({ role: 'character', character: '小狐', look: '', count: 4 })
    expect(String(lastSave()[1].nodes![0].data.prompt)).toContain('红色围巾')
    expect(await screen.findByRole('article', { name: '角色 小狐' })).toBeInTheDocument()
  })
})

describe('Character library panel', () => {
  it('lists library characters, filters by search and marks the ones already in the project', async () => {
    renderPage()
    const panel = within(await screen.findByRole('complementary', { name: '角色库' }))
    expect(await panel.findByText('阿丁')).toBeInTheDocument()
    expect(panel.getByText('2 套造型')).toBeInTheDocument()
    expect(panel.getByText('已在本项目')).toBeInTheDocument()
    expect(panel.queryByRole('button', { name: '加入 狐妖' })).not.toBeInTheDocument()
    expect(panel.getByRole('link', { name: '管理' })).toHaveAttribute('href', '/library')
    fireEvent.change(panel.getByRole('searchbox'), { target: { value: '鹊桥' } })
    expect(panel.getByText('织女')).toBeInTheDocument()
    expect(panel.queryByText('阿丁')).not.toBeInTheDocument()
    fireEvent.change(panel.getByRole('searchbox'), { target: { value: '没有这个人' } })
    expect(panel.getByText('没有匹配的角色')).toBeInTheDocument()
  })

  it('adds a library character with the current revision, then reloads the canvas', async () => {
    renderPage()
    const panel = within(await screen.findByRole('complementary', { name: '角色库' }))
    const before = api.get.mock.calls.length
    fireEvent.click(await panel.findByRole('button', { name: '加入 阿丁' }))
    await waitFor(() => expect(lib.addToProject).toHaveBeenCalledWith('c1', 'lib-ding', 3))
    await waitFor(() => expect(api.get.mock.calls.length).toBe(before + 1))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('已加入「阿丁」', 'success'))
  })

  it('reloads and asks to retry when the canvas changed elsewhere (409)', async () => {
    lib.addToProject.mockRejectedValue(new ConflictError('画布已在其他窗口更新'))
    renderPage()
    const panel = within(await screen.findByRole('complementary', { name: '角色库' }))
    const before = api.get.mock.calls.length
    fireEvent.click(await panel.findByRole('button', { name: '加入 织女' }))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('画布在别处被修改，已刷新，请再试一次', 'error'))
    await waitFor(() => expect(api.get.mock.calls.length).toBe(before + 1))
  })

  it('locks editing while a character is being added, so nothing edited meanwhile is lost', async () => {
    let finish: (value: { revision: number; node_ids: string[] }) => void = () => undefined
    lib.addToProject.mockImplementation(() => new Promise((done) => { finish = done }))
    renderPage()
    const panel = within(await screen.findByRole('complementary', { name: '角色库' }))
    fireEvent.click(await panel.findByRole('button', { name: '加入 阿丁' }))
    expect(await screen.findByText('正在加入角色，请稍候…')).toBeInTheDocument()
    expect(within(card('书生')).getByRole('button', { name: '编辑' })).toBeDisabled()
    expect(within(card('书生')).getByRole('button', { name: '选择第 1 张' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '新建角色' })).toBeDisabled()
    finish({ revision: 4, node_ids: ['n1'] })
    await waitFor(() => expect(screen.queryByText('正在加入角色，请稍候…')).not.toBeInTheDocument())
    expect(within(card('书生')).getByRole('button', { name: '编辑' })).toBeEnabled()
  })

  it('uploads your own character image from the panel and refreshes the list', async () => {
    lib.upload.mockResolvedValue(libraryItems()[1])
    renderPage()
    const panel = within(await screen.findByRole('complementary', { name: '角色库' }))
    fireEvent.click(panel.getByRole('button', { name: '上传自己的角色图' }))
    const dialog = await screen.findByRole('dialog', { name: '上传自己的角色图' })
    const submit = within(dialog).getByRole('button', { name: '上传' })
    expect(submit).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('角色名'), { target: { value: '小满' } })
    fireEvent.change(within(dialog).getByLabelText('外貌描述（选填）'), { target: { value: '圆脸' } })
    const file = new File(['x'], 'a.png', { type: 'image/png' })
    fireEvent.change(within(dialog).getByLabelText('角色图'), { target: { files: [file] } })
    await waitFor(() => expect(submit).toBeEnabled())
    const before = lib.list.mock.calls.length
    fireEvent.click(submit)
    await waitFor(() => expect(lib.upload).toHaveBeenCalledWith({ name: '小满', description: '圆脸', file }))
    await waitFor(() => expect(lib.list.mock.calls.length).toBe(before + 1))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('blocks a wrong file type or an oversized file in the upload dialog', async () => {
    renderPage()
    const panel = within(await screen.findByRole('complementary', { name: '角色库' }))
    fireEvent.click(panel.getByRole('button', { name: '上传自己的角色图' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('角色名'), { target: { value: '小满' } })
    fireEvent.change(within(dialog).getByLabelText('角色图'), { target: { files: [new File(['x'], 'a.gif', { type: 'image/gif' })] } })
    expect(within(dialog).getByRole('alert')).toHaveTextContent('只支持 PNG / JPEG / WebP')
    expect(within(dialog).getByRole('button', { name: '上传' })).toBeDisabled()
    const big = new File(['x'], 'b.png', { type: 'image/png' })
    Object.defineProperty(big, 'size', { value: 11 * 1024 * 1024 })
    fireEvent.change(within(dialog).getByLabelText('角色图'), { target: { files: [big] } })
    expect(within(dialog).getByRole('alert')).toHaveTextContent('10MB')
    expect(within(dialog).getByRole('button', { name: '上传' })).toBeDisabled()
  })
})
