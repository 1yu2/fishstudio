import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ConflictError, nodeCanvasApi, type StoredNode } from '../../api/nodeCanvas'
import { scriptApi, type ScriptBlock, type ScriptPayload, type ScriptRevision } from '../../api/script'
import type { FooterOverride } from '../project/outletContext'
import { confirmDialog } from '../ui/ConfirmDialog'
import { toast } from '../ui/Toast'
import ScriptPage from './ScriptPage'

vi.mock('../../api/nodeCanvas', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/nodeCanvas')>()
  return { ...actual, nodeCanvasApi: { get: vi.fn(), save: vi.fn(), split: vi.fn() } }
})
vi.mock('../../api/script', () => ({
  scriptApi: { get: vi.fn(), revision: vi.fn(), save: vi.fn(), saveKeepalive: vi.fn(), restore: vi.fn(), importText: vi.fn(), draft: vi.fn(), rewrite: vi.fn() },
}))
vi.mock('../ui/Toast', () => ({ toast: vi.fn() }))
vi.mock('../ui/ConfirmDialog', () => ({ confirmDialog: vi.fn() }))

const script = vi.mocked(scriptApi)
const canvas = vi.mocked(nodeCanvasApi)
const confirm = vi.mocked(confirmDialog)

const long = '字'.repeat(31)
/** 给没有 id 的块补确定的 id（线上由前端生成） */
type Loose = { acts: { title: string; scenes: { id: string; title: string; heading: string; blocks: (Omit<ScriptBlock, 'id'> & { id?: string })[] }[] }[] }
const withIds = (value: Loose): ScriptPayload => {
  let n = 0
  return { acts: value.acts.map((act) => ({ ...act, scenes: act.scenes.map((scene) => ({ ...scene, blocks: scene.blocks.map((block) => ({ ...block, id: block.id || `t${++n}` })) })) })) }
}
const payload = (): ScriptPayload => withIds({
  acts: [
    {
      title: '第一幕 · 相遇',
      scenes: [
        { id: 's1', title: '雪夜竹林', heading: '外景 · 夜 · 大雪', blocks: [
          { type: 'action', character: '', text: '书生提着灯笼赶路。' },
          { type: 'dialogue', character: '狐妖', text: '你终于来了。我等了你三百年。' },
        ] },
        { id: 's2', title: '破庙避雪', heading: '', blocks: [{ type: 'dialogue', character: '书生', text: long }] },
      ],
    },
    { title: '第二幕 · 真相', scenes: [{ id: 's3', title: '灯下露尾', heading: '', blocks: [{ type: 'action', character: '', text: '灯火摇曳。' }] }] },
  ],
})

const revisionOf = (value: ScriptPayload, revision: number, source: ScriptRevision['source'] = 'user'): ScriptRevision => ({
  revision, payload: value, source, word_count: 10, edit_seq: 1, updated_at: '2026-11-04T10:00:00Z',
})
const textNode = (text: string): StoredNode => ({ id: 't1', type: 'text', position: { x: 0, y: 0 }, data: { text } })
const shotNodes = (): StoredNode[] => [
  { id: 'k1', type: 'image', position: { x: 0, y: 0 }, data: { label: '1 · 开场 关键帧' } },
  { id: 'v1', type: 'video', position: { x: 400, y: 0 }, data: { label: '1 · 开场' } },
]
const shotEdges = () => [{ id: 'e1', source: 'k1', target: 'v1', sourceHandle: 'out', targetHandle: 'first_frame' }]

function setup(options: { script?: ScriptRevision | null; nodes?: StoredNode[]; edges?: ReturnType<typeof shotEdges> } = {}) {
  const current = options.script === undefined ? revisionOf(payload(), 3) : options.script
  script.get.mockResolvedValue({ current, revisions: current ? [{ revision: current.revision, source: current.source, word_count: 10, created_at: current.updated_at, updated_at: current.updated_at }] : [] })
  script.save.mockImplementation(async (_id, body, based, source) => ({ ...revisionOf(body, (based.revision ?? 0) + 1, source), edit_seq: (based.seq ?? 0) + 1 }))
  canvas.get.mockResolvedValue({ id: 'c1', title: '雪夜狐妖', aspect_ratio: '9:16', node_count: 0, updated_at: null, created_at: null, revision: 7, graph: { nodes: options.nodes ?? [], edges: options.edges ?? [] } })
  canvas.save.mockResolvedValue({ revision: 8 })
  canvas.split.mockResolvedValue({ characters: [{ name: '狐妖', description: '白衣' }], shots: [{ title: '开场', prompt: '雪夜', characters: ['狐妖'], duration_s: 4 }] })
  confirm.mockResolvedValue(true)
}

const refreshSummary = vi.fn()

function Frame() {
  const [footer, setFooter] = useState<FooterOverride | null>(null)
  return (
    <>
      <Outlet context={{ summary: null, loading: false, refreshSummary, setFooter }} />
      <footer aria-label="底栏">
        {footer?.hint && <span>{footer.hint}</span>}
        {footer?.note && <span>{footer.note}</span>}
        {footer && <button type="button" disabled={footer.disabled || footer.busy} onClick={footer.onClick}>{footer.label}</button>}
      </footer>
    </>
  )
}
function Where() { return <div aria-label="location">{useLocation().pathname}</div> }

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/studio/c1/script']} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/studio/:canvasId" element={<Frame />}>
          <Route path="script" element={<ScriptPage />} />
          <Route path="characters" element={<div>CHARACTERS_PAGE</div>} />
          <Route path="storyboard" element={<div>STORYBOARD_PAGE</div>} />
        </Route>
      </Routes>
      <Where />
    </MemoryRouter>,
  )
}

const lastSave = () => script.save.mock.calls[script.save.mock.calls.length - 1]
const scrollIntoView = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  window.HTMLElement.prototype.scrollIntoView = scrollIntoView
  setup()
})

describe('ScriptPage outline', () => {
  it('lists acts and scenes with the total and jumps to a scene on click', async () => {
    renderPage()
    const outline = await screen.findByRole('complementary', { name: '大纲' })
    expect(within(outline).getAllByText(/^\d+ 场 · 约 \d+ 秒$/).length).toBeGreaterThan(0)
    expect(within(outline).getByLabelText('第 1 幕标题')).toHaveValue('第一幕 · 相遇')
    expect(within(outline).getByLabelText('第 2 幕标题')).toHaveValue('第二幕 · 真相')
    const first = within(outline).getByRole('button', { name: /场 1 · 雪夜竹林/ })
    expect(first).toHaveAttribute('aria-current', 'true')
    fireEvent.click(within(outline).getByRole('button', { name: /场 3 · 灯下露尾/ }))
    expect(within(outline).getByRole('button', { name: /场 3 · 灯下露尾/ })).toHaveAttribute('aria-current', 'true')
    expect(first).not.toHaveAttribute('aria-current')
    expect(scrollIntoView).toHaveBeenCalled()
    expect(scrollIntoView.mock.instances[scrollIntoView.mock.instances.length - 1]).toBe(document.getElementById('scene-s3'))
  })

  it('renames an act and saves the new title', async () => {
    renderPage()
    fireEvent.change(await screen.findByLabelText('第 2 幕标题'), { target: { value: '第二幕 · 离别' } })
    await waitFor(() => expect(script.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(lastSave()[1].acts[1].title).toBe('第二幕 · 离别')
  })

  it('adds a scene to the end of the active act, then an act', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /场 3 · 灯下露尾/ }))
    fireEvent.click(screen.getByRole('button', { name: '+ 添加一场' }))
    expect(await screen.findByLabelText('场 4 标题')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '+ 添加一幕' }))
    expect(await screen.findByLabelText('第 3 幕标题')).toHaveValue('第三幕')
    await waitFor(() => expect(script.save).toHaveBeenCalled(), { timeout: 3000 })
    const acts = lastSave()[1].acts
    expect(acts[1].scenes.map((item) => item.id)).toEqual(['s3', 's4'])
    expect(acts[2].scenes).toHaveLength(1)
  })

  it('moves a scene down and deletes one only after confirming', async () => {
    renderPage()
    await screen.findByRole('complementary', { name: '大纲' })
    expect(screen.getByRole('button', { name: '上移场 1' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '下移场 1' }))
    await waitFor(() => expect(screen.getByLabelText('场 1 标题')).toHaveValue('破庙避雪'))
    confirm.mockResolvedValueOnce(false)
    fireEvent.click(screen.getByRole('button', { name: '删除场 3' }))
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(screen.getByLabelText('场 3 标题')).toHaveValue('灯下露尾')
    fireEvent.click(screen.getByRole('button', { name: '删除场 3' }))
    await waitFor(() => expect(screen.queryByLabelText('场 3 标题')).not.toBeInTheDocument())
    expect(confirm.mock.calls[1][0]).toMatchObject({ danger: true })
    await waitFor(() => expect(script.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(lastSave()[1].acts.flatMap((a) => a.scenes.map((s) => s.id))).toEqual(['s2', 's1'])
  })
})

describe('ScriptPage editing and saving', () => {
  it('shows version, state and word count; saves 1s after editing with based_on, then uses the new revision', async () => {
    renderPage()
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent(/版本 v3 · 已保存 · [\d,]+ 字/)
    const box = screen.getByLabelText('场 1 块 1 动作')
    fireEvent.change(box, { target: { value: '书生提着灯笼，在雪里赶路。' } })
    expect(status).toHaveTextContent('未保存')
    expect(script.save).not.toHaveBeenCalled()
    await waitFor(() => expect(script.save).toHaveBeenCalledTimes(1), { timeout: 3000 })
    expect(lastSave()[0]).toBe('c1')
    expect(lastSave()[2]).toMatchObject({ revision: 3, seq: 1 })
    expect(lastSave()[1].acts[0].scenes[0].blocks[0].text).toBe('书生提着灯笼，在雪里赶路。')
    await waitFor(() => expect(status).toHaveTextContent('版本 v4 · 已保存'))
    fireEvent.change(box, { target: { value: '再改一次' } })
    await waitFor(() => expect(script.save).toHaveBeenCalledTimes(2), { timeout: 3000 })
    expect(lastSave()[2]).toMatchObject({ revision: 4, seq: 2 })
  })

  it('on 409 refreshes from the server and says so', async () => {
    script.save.mockRejectedValueOnce(new ConflictError('剧本在别处被修改'))
    renderPage()
    fireEvent.change(await screen.findByLabelText('场 1 块 1 动作'), { target: { value: '我的改动' } })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('剧本在别处被修改，已刷新', 'error'), { timeout: 3000 })
    await waitFor(() => expect(script.get).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.getByLabelText('场 1 块 1 动作')).toHaveValue('书生提着灯笼赶路。'))
    expect(screen.getByRole('status')).toHaveTextContent('已保存')
  })

  it('shows a failed state when saving errors', async () => {
    script.save.mockRejectedValueOnce(new Error('网络错误'))
    renderPage()
    fireEvent.change(await screen.findByLabelText('场 1 块 1 动作'), { target: { value: 'x' } })
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('保存失败'), { timeout: 3000 })
    expect(toast).toHaveBeenCalledWith('网络错误', 'error')
  })

  it('asks before leaving with unsaved edits and keepalive-saves the latest on pagehide', async () => {
    renderPage()
    await screen.findByLabelText('场 1 块 1 动作')
    const quiet = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(quiet)
    expect(quiet.defaultPrevented).toBe(false)
    fireEvent.change(screen.getByLabelText('场 1 块 1 动作'), { target: { value: '没保存' } })
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    window.dispatchEvent(new Event('pagehide'))
    expect(script.saveKeepalive).toHaveBeenCalledTimes(1)
    expect(script.saveKeepalive.mock.calls[0][1].acts[0].scenes[0].blocks[0].text).toBe('没保存')
    expect(script.saveKeepalive.mock.calls[0][2]).toMatchObject({ revision: 3, seq: 1 })
  })

  it('inserts, moves and deletes blocks, and edits dialogue speaker with name suggestions', async () => {
    renderPage()
    await screen.findByLabelText('场 1 块 1 动作')
    expect(document.querySelectorAll('#sc-characters option')).toHaveLength(2)
    fireEvent.click(within(screen.getByRole('group', { name: '场 1 在块 1 后添加' })).getByRole('button', { name: '+ 对白' }))
    const speaker = await screen.findByLabelText('场 1 块 2 对白角色名')
    fireEvent.change(speaker, { target: { value: '书生' } })
    fireEvent.change(screen.getByLabelText('场 1 块 2 对白'), { target: { value: '姑娘认错人了吧？' } })
    fireEvent.click(screen.getByRole('button', { name: '上移场 1 块 2' }))
    await waitFor(() => expect(screen.getByLabelText('场 1 块 1 对白')).toHaveValue('姑娘认错人了吧？'))
    fireEvent.click(within(screen.getByRole('group', { name: '场 1 在块 3 后添加' })).getByRole('button', { name: '+ 动作' }))
    expect(await screen.findByLabelText('场 1 块 4 动作')).toHaveValue('')
    fireEvent.click(screen.getByRole('button', { name: '删除场 1 块 4' }))
    await waitFor(() => expect(screen.queryByLabelText('场 1 块 4 动作')).not.toBeInTheDocument())
    await waitFor(() => expect(script.save).toHaveBeenCalled(), { timeout: 3000 })
    const blocks = lastSave()[1].acts[0].scenes[0].blocks
    expect(blocks.map((item) => [item.type, item.character, item.text])).toEqual([
      ['dialogue', '书生', '姑娘认错人了吧？'], ['action', '', '书生提着灯笼赶路。'], ['dialogue', '狐妖', '你终于来了。我等了你三百年。'],
    ])
  })
})

describe('ScriptPage AI rewrite of a selection', () => {
  const select = (box: HTMLElement, start: number, end: number) => {
    const area = box as HTMLTextAreaElement
    area.focus()
    area.setSelectionRange(start, end)
    fireEvent.select(area)
  }

  it('rewrites only the selected text after confirming the suggestion', async () => {
    script.rewrite.mockResolvedValue({ text: '你总算来了' })
    renderPage()
    const box = await screen.findByLabelText('场 1 块 2 对白')
    expect(screen.queryByRole('toolbar', { name: '选中文字操作' })).not.toBeInTheDocument()
    select(box, 0, 5)  // “你终于来了”
    const bar = screen.getByRole('toolbar', { name: '选中文字操作' })
    expect(within(bar).getByText('会调用 AI')).toBeInTheDocument()
    fireEvent.click(within(bar).getByRole('button', { name: 'AI 改写' }))
    const suggestion = await screen.findByRole('group', { name: 'AI 建议' })
    await within(suggestion).findByText('你总算来了')
    expect(script.rewrite).toHaveBeenCalledWith('c1', { text: '你终于来了', mode: 'rewrite', context: '你终于来了。我等了你三百年。' })
    expect(within(suggestion).getByText('你终于来了')).toBeInTheDocument()
    fireEvent.click(within(suggestion).getByRole('button', { name: '替换' }))
    expect(screen.getByLabelText('场 1 块 2 对白')).toHaveValue('你总算来了。我等了你三百年。')
    expect(screen.queryByRole('group', { name: 'AI 建议' })).not.toBeInTheDocument()
  })

  it('cancel keeps the original text; shorten and tone pass their mode', async () => {
    script.rewrite.mockResolvedValue({ text: '短' })
    renderPage()
    const box = await screen.findByLabelText('场 1 块 2 对白')
    select(box, 6, 13)
    fireEvent.click(within(screen.getByRole('toolbar', { name: '选中文字操作' })).getByRole('button', { name: '缩短' }))
    fireEvent.click(await within(await screen.findByRole('group', { name: 'AI 建议' })).findByRole('button', { name: '取消' }))
    expect(box).toHaveValue('你终于来了。我等了你三百年。')
    expect(script.rewrite.mock.calls[0][1]).toMatchObject({ mode: 'shorten', text: '我等了你三百年' })
    select(box, 0, 3)
    const bar = screen.getByRole('toolbar', { name: '选中文字操作' })
    fireEvent.click(within(bar).getByRole('button', { name: /换语气/ }))
    expect(within(bar).getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['更口语', '更古风', '更紧张', '更温柔'])
    fireEvent.click(within(bar).getByRole('menuitem', { name: '更古风' }))
    await waitFor(() => expect(script.rewrite).toHaveBeenCalledTimes(2))
    expect(script.rewrite.mock.calls[1][1]).toMatchObject({ mode: 'tone', tone: '更古风' })
  })

  it('shows the error and leaves the text alone when the AI is unavailable', async () => {
    script.rewrite.mockRejectedValue(new Error('AI 暂时不可用，请稍后再试'))
    renderPage()
    const box = await screen.findByLabelText('场 1 块 2 对白')
    select(box, 0, 3)
    fireEvent.click(within(screen.getByRole('toolbar', { name: '选中文字操作' })).getByRole('button', { name: 'AI 改写' }))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('AI 暂时不可用，请稍后再试', 'error'))
    expect(box).toHaveValue('你终于来了。我等了你三百年。')
  })
})

describe('ScriptPage history', () => {
  it('lists versions, previews one read-only and restores it', async () => {
    const old = payload()
    old.acts[0].scenes[0].blocks[0].text = '最初的开场。'
    script.get.mockResolvedValueOnce({
      current: revisionOf(payload(), 3),
      revisions: [
        { revision: 3, source: 'user', word_count: 1240, created_at: '2026-11-04T10:00:00Z', updated_at: '2026-11-04T10:00:00Z' },
        { revision: 2, source: 'ai_draft', word_count: 800, created_at: '2026-11-03T10:00:00Z', updated_at: '2026-11-03T10:00:00Z' },
      ],
    })
    script.revision.mockResolvedValue(revisionOf(old, 2, 'ai_draft'))
    script.restore.mockResolvedValue(revisionOf(old, 4, 'restore'))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '历史版本' }))
    const drawer = await screen.findByRole('dialog', { name: '历史版本' })
    expect(within(drawer).getByText(/v3 · 当前/)).toBeInTheDocument()
    expect(within(drawer).getByText('手动编辑')).toBeInTheDocument()
    expect(within(drawer).getByText('AI 起草')).toBeInTheDocument()
    expect(within(drawer).getByText(/1,240 字/)).toBeInTheDocument()
    fireEvent.click(within(drawer).getByText(/^v2$/))
    expect(await within(drawer).findByLabelText('v2 内容')).toHaveTextContent('最初的开场。')
    expect(script.revision).toHaveBeenCalledWith('c1', 2)
    script.get.mockResolvedValueOnce({ current: revisionOf(old, 4, 'restore'), revisions: [] })
    fireEvent.click(within(drawer).getByRole('button', { name: '恢复这一版' }))
    await waitFor(() => expect(script.restore).toHaveBeenCalledWith('c1', 2))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '历史版本' })).not.toBeInTheDocument())
    expect(screen.getByLabelText('场 1 块 1 动作')).toHaveValue('最初的开场。')
    expect(screen.getByRole('status')).toHaveTextContent('版本 v4')
  })

  it('does not offer to restore the current version', async () => {
    script.revision.mockResolvedValue(revisionOf(payload(), 3))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '历史版本' }))
    const drawer = await screen.findByRole('dialog', { name: '历史版本' })
    fireEvent.click(within(drawer).getByText(/v3 · 当前/))
    await within(drawer).findByLabelText('v3 内容')
    expect(within(drawer).queryByRole('button', { name: '恢复这一版' })).not.toBeInTheDocument()
  })
})

describe('ScriptPage import', () => {
  const imported = (): ScriptPayload => withIds({
    acts: [{ title: '第一幕', scenes: [
      { id: 's1', title: '开场', heading: '内景 · 夜', blocks: [{ type: 'action', character: '', text: '雨夜。' }, { type: 'dialogue', character: '甲', text: '你好' }] },
      { id: 's2', title: '', heading: '', blocks: [{ type: 'dialogue', character: '乙', text: '再见' }] },
    ] }],
  })

  it('previews the parsed counts, confirms before replacing existing content and saves as an import', async () => {
    script.importText.mockResolvedValue({ payload: imported() })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '粘贴已有剧本' }))
    const dialog = await screen.findByRole('dialog', { name: '粘贴已有剧本' })
    expect(within(dialog).getByRole('button', { name: '用这份剧本替换' })).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('剧本文本'), { target: { value: '第一幕\n场 1 开场\n甲：你好' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '解析预览' }))
    const result = await within(dialog).findByLabelText('解析结果')
    expect(result).toHaveTextContent('1 幕 · 2 场 · 2 句对白')
    expect(result).toHaveTextContent('场 1 · 开场（内景 · 夜）：雨夜。')
    expect(script.importText).toHaveBeenCalledWith('c1', '第一幕\n场 1 开场\n甲：你好')
    fireEvent.click(within(dialog).getByRole('button', { name: '用这份剧本替换' }))
    await waitFor(() => expect(script.save).toHaveBeenCalledTimes(1))
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(lastSave()[1]).toEqual(imported())
    expect(lastSave()[2]).toMatchObject({ revision: 3, seq: 1 })
    expect(lastSave()[3]).toBe('import')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '粘贴已有剧本' })).not.toBeInTheDocument())
    expect(screen.getByLabelText('场 2 块 1 对白')).toHaveValue('再见')
  })

  it('keeps the current script when the replace confirmation is declined', async () => {
    script.importText.mockResolvedValue({ payload: imported() })
    confirm.mockResolvedValue(false)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '粘贴已有剧本' }))
    const dialog = await screen.findByRole('dialog', { name: '粘贴已有剧本' })
    fireEvent.change(within(dialog).getByLabelText('剧本文本'), { target: { value: 'x' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '解析预览' }))
    await within(dialog).findByLabelText('解析结果')
    fireEvent.click(within(dialog).getByRole('button', { name: '用这份剧本替换' }))
    await waitFor(() => expect(confirm).toHaveBeenCalled())
    expect(script.save).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: '粘贴已有剧本' })).toBeInTheDocument()
  })

  it('shows the parse error', async () => {
    script.importText.mockRejectedValue(new Error('文本最多 50000 字'))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '粘贴已有剧本' }))
    const dialog = await screen.findByRole('dialog', { name: '粘贴已有剧本' })
    fireEvent.change(within(dialog).getByLabelText('剧本文本'), { target: { value: 'x' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '解析预览' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('文本最多 50000 字')
  })
})

describe('ScriptPage empty state', () => {
  beforeEach(() => setup({ script: null, nodes: [textNode('雪夜竹林，狐妖等了书生三百年')] }))

  it('offers the three entries and a link to the storyboard', async () => {
    renderPage()
    const empty = await screen.findByRole('region', { name: '还没有剧本' })
    expect(within(empty).getByRole('button', { name: /用 AI 写剧本（会调用 AI）/ })).toBeInTheDocument()
    expect(within(empty).getByRole('button', { name: '粘贴已有剧本' })).toBeInTheDocument()
    expect(within(empty).getByRole('button', { name: '自己从空白开始' })).toBeInTheDocument()
    expect(within(empty).getByRole('link', { name: '直接拆镜头 →' })).toHaveAttribute('href', '/studio/c1/storyboard')
    await waitFor(() => expect(within(empty).getByLabelText('剧本想法')).toHaveValue('雪夜竹林，狐妖等了书生三百年'))
  })

  it('drafts with AI from the canvas idea, previews, then saves it as an AI draft', async () => {
    const draft: ScriptPayload = withIds({ acts: [{ title: '第一幕', scenes: [
      { id: 's1', title: '雪夜', heading: '外景 · 夜', blocks: [{ type: 'action', character: '', text: '大雪压竹。' }] },
      { id: 's2', title: '破庙', heading: '', blocks: [{ type: 'action', character: '', text: '火光。' }] },
    ] }] })
    script.draft.mockResolvedValue({ payload: draft })
    renderPage()
    const empty = await screen.findByRole('region', { name: '还没有剧本' })
    await waitFor(() => expect(within(empty).getByLabelText('剧本想法')).toHaveValue('雪夜竹林，狐妖等了书生三百年'))
    fireEvent.change(within(empty).getByRole('spinbutton'), { target: { value: '2' } })
    fireEvent.click(within(empty).getByRole('button', { name: /用 AI 写剧本/ }))
    const preview = await within(empty).findByLabelText('AI 起草预览')
    expect(script.draft).toHaveBeenCalledWith('c1', '雪夜竹林，狐妖等了书生三百年', 2)
    expect(preview).toHaveTextContent('1 幕 · 2 场')
    expect(script.save).not.toHaveBeenCalled()
    fireEvent.click(within(preview).getByRole('button', { name: '使用这份剧本' }))
    await waitFor(() => expect(script.save).toHaveBeenCalledTimes(1))
    expect(lastSave()[1]).toEqual(draft)
    expect(lastSave()[2]).toMatchObject({ revision: null, seq: null })
    expect(lastSave()[3]).toBe('ai_draft')
    expect(await screen.findByLabelText('场 2 标题')).toHaveValue('破庙')
    expect(screen.getByRole('status')).toHaveTextContent('版本 v1')
  })

  it('reports an AI failure without leaving the empty state', async () => {
    script.draft.mockRejectedValue(new Error('AI 暂时不可用，请稍后再试'))
    renderPage()
    const empty = await screen.findByRole('region', { name: '还没有剧本' })
    await waitFor(() => expect(within(empty).getByLabelText('剧本想法')).toHaveValue('雪夜竹林，狐妖等了书生三百年'))
    fireEvent.click(within(empty).getByRole('button', { name: /用 AI 写剧本/ }))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('AI 暂时不可用，请稍后再试', 'error'))
    expect(screen.getByRole('region', { name: '还没有剧本' })).toBeInTheDocument()
  })

  it('starts from blank with one act and one scene, and the import entry opens the dialog', async () => {
    renderPage()
    const empty = await screen.findByRole('region', { name: '还没有剧本' })
    fireEvent.click(within(empty).getByRole('button', { name: '粘贴已有剧本' }))
    expect(await screen.findByRole('dialog', { name: '粘贴已有剧本' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '关闭粘贴剧本' }))
    fireEvent.click(within(empty).getByRole('button', { name: '自己从空白开始' }))
    expect(await screen.findByLabelText('场 1 标题')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '还没有剧本' })).not.toBeInTheDocument()
    await waitFor(() => expect(script.save).toHaveBeenCalled(), { timeout: 3000 })
    expect(lastSave()[1].acts).toHaveLength(1)
    expect(lastSave()[1].acts[0].scenes).toHaveLength(1)
    expect(lastSave()[2]).toMatchObject({ revision: null, seq: null })
  })
})

describe('ScriptPage footer', () => {
  it('without shots: flushes, splits the flattened script (scenes x 2 shots), lays it into the canvas and goes to characters', async () => {
    renderPage()
    const button = await screen.findByRole('button', { name: '确认剧本，拆出角色和分镜 →' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.change(screen.getByLabelText('场 1 块 1 动作'), { target: { value: '改过的开场。' } })  // 未保存的改动要先保存
    let release: () => void = () => undefined
    canvas.split.mockImplementationOnce(() => new Promise((resolve) => {
      release = () => resolve({ characters: [{ name: '狐妖', description: '白衣' }], shots: [{ title: '开场', prompt: '雪夜', characters: ['狐妖'], duration_s: 4 }] })
    }))
    fireEvent.click(button)
    expect(await screen.findByRole('button', { name: '正在拆分…' })).toBeDisabled()
    await waitFor(() => expect(canvas.split).toHaveBeenCalled())
    release()
    await waitFor(() => expect(screen.getByLabelText('location')).toHaveTextContent('/studio/c1/characters'))
    expect(script.save).toHaveBeenCalledTimes(1)
    const [, idea, shots] = canvas.split.mock.calls[0]
    expect(canvas.split.mock.calls[0][0]).toBe('c1')
    expect(idea).toContain('第一幕 · 相遇')
    expect(idea).toContain('场 1 雪夜竹林\n外景 · 夜 · 大雪\n改过的开场。\n狐妖：你终于来了。我等了你三百年。')
    expect(shots).toBe(6)
    expect(script.save.mock.invocationCallOrder[0]).toBeLessThan(canvas.split.mock.invocationCallOrder[0])
    const [, graph, revision] = canvas.save.mock.calls[0]
    expect(revision).toBe(7)
    expect(graph.nodes!.some((item) => item.type === 'video')).toBe(true)
    expect(graph.edges!.length).toBeGreaterThan(0)
    expect(refreshSummary).toHaveBeenCalled()
  })

  it('retries the canvas save once on a 409 using the fresh canvas', async () => {
    canvas.save.mockRejectedValueOnce(new ConflictError('画布已变化'))
    renderPage()
    const button = await screen.findByRole('button', { name: '确认剧本，拆出角色和分镜 →' })
    await waitFor(() => expect(button).toBeEnabled())
    canvas.get.mockClear()
    fireEvent.click(button)
    await waitFor(() => expect(screen.getByLabelText('location')).toHaveTextContent('/studio/c1/characters'))
    expect(canvas.save).toHaveBeenCalledTimes(2)
    expect(canvas.get).toHaveBeenCalledTimes(2)
    expect(canvas.split).toHaveBeenCalledTimes(1)
  })

  it('stays on the page and reports when splitting fails', async () => {
    canvas.split.mockRejectedValue(new Error('拆分失败了'))
    renderPage()
    const button = await screen.findByRole('button', { name: '确认剧本，拆出角色和分镜 →' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    await waitFor(() => expect(toast).toHaveBeenCalledWith('拆分失败了', 'error'))
    expect(canvas.save).not.toHaveBeenCalled()
    expect(screen.getByLabelText('location')).toHaveTextContent('/studio/c1/script')
    expect(await screen.findByRole('button', { name: '确认剧本，拆出角色和分镜 →' })).toBeEnabled()
  })

  it('with shots: confirms straight to characters, and offers an append re-split on the page', async () => {
    setup({ nodes: shotNodes(), edges: shotEdges() })
    renderPage()
    const button = await screen.findByRole('button', { name: '剧本已确认，去定角色 →' })
    fireEvent.click(await screen.findByRole('button', { name: '按剧本重新拆分（追加镜头）' }))
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(canvas.save).toHaveBeenCalledTimes(1))
    expect(canvas.split).toHaveBeenCalledTimes(1)
    expect(canvas.save.mock.calls[0][1].nodes!.length).toBeGreaterThan(2)  // 追加，不删已有镜头
    expect(screen.getByLabelText('location')).toHaveTextContent('/studio/c1/script')
    fireEvent.click(button)
    await waitFor(() => expect(screen.getByLabelText('location')).toHaveTextContent('/studio/c1/characters'))
    expect(canvas.split).toHaveBeenCalledTimes(1)
  })

  it('does not re-split when the confirmation is declined', async () => {
    setup({ nodes: shotNodes(), edges: shotEdges() })
    confirm.mockResolvedValue(false)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '按剧本重新拆分（追加镜头）' }))
    await waitFor(() => expect(confirm).toHaveBeenCalled())
    expect(canvas.split).not.toHaveBeenCalled()
  })

  it('is disabled with 先写剧本 while the script is empty', async () => {
    setup({ script: revisionOf(withIds({ acts: [{ title: '第一幕', scenes: [{ id: 's1', title: '', heading: '', blocks: [{ id: '', type: 'action', character: '', text: '  ' }] }] }] }), 1) })
    renderPage()
    const button = await screen.findByRole('button', { name: '确认剧本，拆出角色和分镜 →' })
    await waitFor(() => expect(screen.getByText('先写剧本')).toBeInTheDocument())
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText('场 1 块 1 动作'), { target: { value: '有内容了' } })
    await waitFor(() => expect(button).toBeEnabled())
    expect(screen.queryByText('先写剧本')).not.toBeInTheDocument()
  })

  it('hints about dialogue longer than 30 characters, and clears the footer on unmount', async () => {
    const { unmount } = renderPage()
    expect(await screen.findByText('场 2 有 1 句对白超过 30 字，配音会偏长')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('场 2 块 1 对白'), { target: { value: '短台词' } })
    await waitFor(() => expect(screen.queryByText(/配音会偏长/)).not.toBeInTheDocument())
    unmount()
    await act(async () => undefined)
  })
})

describe('ScriptPage narrow layout', () => {
  it('the outline has a toggle that expands it', async () => {
    renderPage()
    const outline = await screen.findByRole('complementary', { name: '大纲' })
    const toggle = within(outline).getByRole('button', { name: /^大纲/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(outline).toHaveClass('sc-outline--open')
  })
})

describe('ScriptPage review fixes', () => {
  const select = (box: HTMLElement, start: number, end: number) => {
    const area = box as HTMLTextAreaElement
    area.focus()
    area.setSelectionRange(start, end)
    fireEvent.select(area)
  }
  const deferred = <T,>() => {
    let resolve: (value: T) => void = () => undefined
    const promise = new Promise<T>((r) => { resolve = r })
    return { promise, resolve }
  }
  const barOf = () => screen.getByRole('toolbar', { name: '选中文字操作' })

  it('never truncates: a replacement that would pass 2000 characters is blocked and the suggestion stays', async () => {
    script.rewrite.mockResolvedValue({ text: '字'.repeat(30) })
    renderPage()
    const box = await screen.findByLabelText('场 1 块 1 动作')
    const text = `前${'字'.repeat(1990)}后`
    fireEvent.change(box, { target: { value: text } })
    select(box, 0, 5)
    fireEvent.click(within(barOf()).getByRole('button', { name: 'AI 改写' }))
    const suggestion = await screen.findByRole('group', { name: 'AI 建议' })
    await within(suggestion).findByText('字'.repeat(30))
    fireEvent.click(within(suggestion).getByRole('button', { name: '替换' }))
    expect(toast).toHaveBeenCalledWith('替换后超过 2000 字，请先缩短', 'error')
    expect(box).toHaveValue(text)
    expect(screen.getByRole('group', { name: 'AI 建议' })).toBeInTheDocument()
  })

  it('discards the suggestion instead of replacing somewhere else when the selected text changed', async () => {
    script.rewrite.mockResolvedValue({ text: '你总算来了' })
    renderPage()
    const box = await screen.findByLabelText('场 1 块 2 对白')
    select(box, 0, 5)
    fireEvent.click(within(barOf()).getByRole('button', { name: 'AI 改写' }))
    const suggestion = await screen.findByRole('group', { name: 'AI 建议' })
    await within(suggestion).findByText('你总算来了')
    fireEvent.change(box, { target: { value: '哦，你终于来了。我等了你三百年。你终于来了。' } })  // 原位置文字变了，别处还有同样的字
    fireEvent.click(within(suggestion).getByRole('button', { name: '替换' }))
    expect(toast).toHaveBeenCalledWith('原文已变化，请重新选择', 'error')
    expect(box).toHaveValue('哦，你终于来了。我等了你三百年。你终于来了。')
    expect(screen.queryByRole('group', { name: 'AI 建议' })).not.toBeInTheDocument()
  })

  it('keeps a pending suggestion with its own block when blocks move, and drops it when the block is deleted', async () => {
    const pending = deferred<{ text: string }>()
    script.rewrite.mockReturnValueOnce(pending.promise)
    renderPage()
    const box = await screen.findByLabelText('场 1 块 2 对白')
    select(box, 0, 5)
    fireEvent.click(within(barOf()).getByRole('button', { name: 'AI 改写' }))
    fireEvent.click(screen.getByRole('button', { name: '上移场 1 块 2' }))  // 对白块移到第 1 位
    await act(async () => pending.resolve({ text: '你总算来了' }))
    const suggestion = await screen.findByRole('group', { name: 'AI 建议' })
    expect(screen.getByLabelText('场 1 块 1 对白').closest('.sc-block')).toContainElement(suggestion)
    fireEvent.click(within(suggestion).getByRole('button', { name: '替换' }))
    expect(screen.getByLabelText('场 1 块 1 对白')).toHaveValue('你总算来了。我等了你三百年。')
    expect(screen.getByLabelText('场 1 块 2 动作')).toHaveValue('书生提着灯笼赶路。')

    const second = deferred<{ text: string }>()
    script.rewrite.mockReturnValueOnce(second.promise)
    select(screen.getByLabelText('场 1 块 1 对白'), 0, 3)
    fireEvent.click(within(barOf()).getByRole('button', { name: '缩短' }))
    fireEvent.click(screen.getByRole('button', { name: '删除场 1 块 1' }))
    await act(async () => second.resolve({ text: '短' }))
    expect(screen.queryByRole('group', { name: 'AI 建议' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('场 1 块 1 动作')).toHaveValue('书生提着灯笼赶路。')
  })

  it('keeps the selection toolbar while focus moves into it, and drops it when focus leaves the block', async () => {
    renderPage()
    const box = await screen.findByLabelText('场 1 块 2 对白')
    select(box, 0, 3)
    const button = within(barOf()).getByRole('button', { name: 'AI 改写' })
    fireEvent.blur(box, { relatedTarget: button })
    expect(screen.getByRole('toolbar', { name: '选中文字操作' })).toBeInTheDocument()
    fireEvent.blur(button, { relatedTarget: document.body })
    expect(screen.queryByRole('toolbar', { name: '选中文字操作' })).not.toBeInTheDocument()
  })

  it('keepalive during an in-flight first save points at that save instead of using a stale base', async () => {
    setup({ script: null, nodes: [] })
    const inflight = deferred<ScriptRevision>()
    script.save.mockReturnValueOnce(inflight.promise)
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '自己从空白开始' }))
    await waitFor(() => expect(script.save).toHaveBeenCalledTimes(1), { timeout: 3000 })
    fireEvent.change(await screen.findByLabelText('场 1 块 1 动作'), { target: { value: '关页前还在打字' } })
    window.dispatchEvent(new Event('pagehide'))
    const sent = script.save.mock.calls[0][2]
    const [, body, base] = script.saveKeepalive.mock.calls[0]
    expect(body.acts[0].scenes[0].blocks[0].text).toBe('关页前还在打字')
    expect(base).toMatchObject({ revision: null, seq: null, supersedes: sent.saveId })
    expect(base.saveId).not.toBe(sent.saveId)
    await act(async () => inflight.resolve({ ...revisionOf(payload(), 1), edit_seq: 1 }))
  })

  it('stays protected as unsaved when a save 409s and the refetch also fails', async () => {
    script.save.mockRejectedValueOnce(new ConflictError('剧本在别处被修改'))
    renderPage()
    fireEvent.change(await screen.findByLabelText('场 1 块 1 动作'), { target: { value: '没保存成功' } })
    script.get.mockRejectedValueOnce(new Error('网络错误'))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('剧本在别处被修改，已刷新', 'error'), { timeout: 3000 })
    await waitFor(() => expect(script.get).toHaveBeenCalledTimes(2))
    const leave = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(leave)
    expect(leave.defaultPrevented).toBe(true)
    window.dispatchEvent(new Event('pagehide'))
    expect(script.saveKeepalive).toHaveBeenCalledTimes(1)
  })

  it('with shots, a failed save keeps you on the page with the error instead of navigating', async () => {
    setup({ nodes: shotNodes(), edges: shotEdges() })
    script.save.mockRejectedValueOnce(new Error('保存炸了'))
    renderPage()
    fireEvent.change(await screen.findByLabelText('场 1 块 1 动作'), { target: { value: '改动' } })
    fireEvent.click(await screen.findByRole('button', { name: '剧本已确认，去定角色 →' }))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('剧本保存失败，已取消操作，请稍后重试', 'error'))
    expect(screen.getByLabelText('location')).toHaveTextContent('/studio/c1/script')
  })

  it('blocks splitting a script longer than the split limit instead of cutting it', async () => {
    const huge = withIds({ acts: [{ title: '第一幕', scenes: [{ id: 's1', title: '长', heading: '', blocks: Array.from({ length: 11 }, () => ({ type: 'action' as const, character: '', text: '字'.repeat(2000) })) }] }] })
    setup({ script: revisionOf(huge, 1) })
    renderPage()
    const button = await screen.findByRole('button', { name: '确认剧本，拆出角色和分镜 →' })
    await waitFor(() => expect(button).toBeEnabled())
    fireEvent.click(button)
    await waitFor(() => expect(toast).toHaveBeenCalledWith('剧本太长，超过 20,000 字，请分集或精简', 'error'))
    expect(canvas.split).not.toHaveBeenCalled()
    expect(screen.getByLabelText('location')).toHaveTextContent('/studio/c1/script')
  })

  it('shows the full text of a long version in the history preview', async () => {
    const huge = withIds({ acts: [{ title: '', scenes: [{ id: 's1', title: '', heading: '', blocks: [{ type: 'action' as const, character: '', text: `${'字'.repeat(1999)}尾` }, ...Array.from({ length: 10 }, () => ({ type: 'action' as const, character: '', text: '字'.repeat(2000) })), { type: 'action' as const, character: '', text: '最后一句' }] }] }] })
    script.revision.mockResolvedValue(revisionOf(huge, 3))
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '历史版本' }))
    fireEvent.click(await screen.findByText(/v3 · 当前/))
    const preview = await screen.findByLabelText('v3 内容')
    expect(preview.textContent).toContain('最后一句')  // 超过 2 万字也不截断
    expect((preview.textContent ?? '').length).toBeGreaterThan(20_000)
  })

  it('import dialog moves focus in, traps Tab, closes on Escape and returns focus to the trigger', async () => {
    renderPage()
    const trigger = await screen.findByRole('button', { name: '粘贴已有剧本' })
    trigger.focus()
    fireEvent.click(trigger)
    const dialog = await screen.findByRole('dialog', { name: '粘贴已有剧本' })
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
    const last = within(dialog).getByRole('button', { name: '取消' })  // 最后一个可聚焦的（“用这份剧本替换”此时是禁用的）
    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: '关闭粘贴剧本' }))
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
    fireEvent.keyDown(dialog, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '粘贴已有剧本' })).not.toBeInTheDocument())
    expect(document.activeElement).toBe(trigger)
  })

  it('history drawer also takes focus, closes on Escape and gives focus back', async () => {
    renderPage()
    const trigger = await screen.findByRole('button', { name: '历史版本' })
    trigger.focus()
    fireEvent.click(trigger)
    const drawer = await screen.findByRole('dialog', { name: '历史版本' })
    expect(drawer).toContainElement(document.activeElement as HTMLElement)
    fireEvent.keyDown(drawer, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '历史版本' })).not.toBeInTheDocument())
    expect(document.activeElement).toBe(trigger)
  })

  it('fills the empty-state idea when the canvas text arrives after mount, unless the user already typed', async () => {
    setup({ script: null, nodes: [] })
    const late = deferred<Awaited<ReturnType<typeof nodeCanvasApi.get>>>()
    canvas.get.mockReturnValueOnce(late.promise)
    const detail = (text: string) => ({ id: 'c1', title: 't', aspect_ratio: '9:16', node_count: 1, updated_at: null, created_at: null, revision: 1, graph: { nodes: [textNode(text)], edges: [] } })
    const { unmount } = renderPage()
    const idea = await screen.findByLabelText('剧本想法')
    expect(idea).toHaveValue('')
    await act(async () => late.resolve(detail('晚到的想法')))
    await waitFor(() => expect(screen.getByLabelText('剧本想法')).toHaveValue('晚到的想法'))
    unmount()

    const late2 = deferred<Awaited<ReturnType<typeof nodeCanvasApi.get>>>()
    canvas.get.mockReturnValueOnce(late2.promise)
    renderPage()
    fireEvent.change(await screen.findByLabelText('剧本想法'), { target: { value: '我自己写的' } })
    await act(async () => late2.resolve(detail('晚到的想法')))
    expect(screen.getByLabelText('剧本想法')).toHaveValue('我自己写的')
  })
})
