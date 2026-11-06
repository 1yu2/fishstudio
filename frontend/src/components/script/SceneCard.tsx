import { useLayoutEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Loader2, Trash2 } from 'lucide-react'
import { scriptApi, type RewriteMode, type ScriptBlock, type ScriptScene } from '../../api/script'
import { toast } from '../ui/Toast'
import { caretPosition, placeBar, scrollParent } from './caret'
import { insertBlock, moveBlock, replaceSelection, sceneSeconds } from './model'

const MAX_BLOCKS = 200
const MAX_BLOCK_CHARS = 2000
const TONES = ['更口语', '更古风', '更紧张', '更温柔']
export const CHARACTER_LIST_ID = 'sc-characters'

/** 自适应高度的 textarea：内容变化后按 scrollHeight 撑开。 */
function AutoText({ value, onChange, onSelect, label, placeholder, className }: {
  value: string
  onChange: (value: string) => void
  onSelect?: (event: React.SyntheticEvent<HTMLTextAreaElement>) => void
  label: string
  placeholder: string
  className?: string
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    if (el.scrollHeight > 0) el.style.height = `${el.scrollHeight}px`
  }, [value])
  return (
    <textarea ref={ref} rows={1} className={`sc-text${className ? ` ${className}` : ''}`} aria-label={label} placeholder={placeholder} maxLength={2000}
      value={value} onChange={(event) => onChange(event.target.value)} onSelect={onSelect} />
  )
}

const BAR_WIDTH = 280

interface Selection { start: number; end: number; text: string; top: number; left: number }
type Suggestion = { selection: Selection; status: 'loading' } | { selection: Selection; status: 'ready'; text: string }

/** 单个块：动作（一个 textarea）或对白（角色名 + 台词）；选中文字后出现 AI 改写小工具条。 */
function BlockEditor({ canvasId, label, block, onChange }: {
  canvasId: string
  label: string
  block: ScriptBlock
  onChange: (patch: Partial<ScriptBlock>) => void
}) {
  const [selection, setSelection] = useState<Selection | null>(null)
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null)
  const [toneOpen, setToneOpen] = useState(false)

  const read = (event: React.SyntheticEvent<HTMLTextAreaElement>) => {
    const { selectionStart, selectionEnd, value } = event.currentTarget
    const text = value.slice(selectionStart, selectionEnd)
    if (!text.trim()) { setSelection(null); setToneOpen(false); return }
    const el = event.currentTarget
    const caret = caretPosition(el, selectionStart)
    // 横向不超出文本框右缘
    const left = Math.max(0, Math.min(caret.left, el.offsetLeft + el.clientWidth - BAR_WIDTH))
    // 纵向按滚动容器的可视边界放：上方放不下就放到所在行下面，并夹在可视区内
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 20
    const lineTop = el.getBoundingClientRect().top + (caret.top - el.offsetTop)
    const box = scrollParent(el)?.getBoundingClientRect()
    const barTop = placeBar(lineTop, lineHeight, box ? box.top : 0, box ? box.bottom : window.innerHeight)
    setSelection({ start: selectionStart, end: selectionEnd, text, top: barTop - lineTop + caret.top, left })
    setToneOpen(false)
  }

  const ask = async (mode: RewriteMode, tone?: string) => {
    if (!selection) return
    const chosen = selection
    setToneOpen(false)
    setSuggestion({ selection: chosen, status: 'loading' })
    try {
      const result = await scriptApi.rewrite(canvasId, { text: chosen.text, mode, ...(tone ? { tone } : {}), context: block.text.slice(0, 2000) })
      setSuggestion({ selection: chosen, status: 'ready', text: result.text })
    } catch (reason) {
      setSuggestion(null)
      toast(reason instanceof Error ? reason.message : 'AI 暂时不可用，请稍后再试', 'error')
    }
  }

  const accept = () => {
    if (suggestion?.status !== 'ready') return
    const { start, end, text: original } = suggestion.selection
    const next = replaceSelection(block.text, start, end, original, suggestion.text)
    if (next === null) {
      // 选中位置上的文字已不是原文：丢弃建议，不去别处找
      toast('原文已变化，请重新选择', 'error')
    } else if (next.length > MAX_BLOCK_CHARS) {
      // 绝不截断：保留建议，让用户取消或先缩短
      toast('替换后超过 2000 字，请先缩短', 'error')
      return
    } else onChange({ text: next })
    setSuggestion(null)
    setSelection(null)
  }

  const keep = (event: React.MouseEvent) => event.preventDefault()  // 点工具条时不让文本框失焦，保留选区
  const text = (
    <AutoText value={block.text} label={label} onChange={(value) => onChange({ text: value })} onSelect={read}
      placeholder={block.type === 'dialogue' ? '台词' : '动作 / 画面描写'} className={block.type === 'dialogue' ? 'sc-text--dialogue' : undefined} />
  )
  return (
    // 焦点移出整个块（含工具条）才清掉选区：键盘用户 Tab 进工具条时选区还在
    <div className={`sc-block sc-block--${block.type}`}
      onBlur={(event) => { if (!suggestion && !event.currentTarget.contains(event.relatedTarget as Node | null)) { setSelection(null); setToneOpen(false) } }}>
      {block.type === 'dialogue' ? (
        <>
          <input className="sc-character" list={CHARACTER_LIST_ID} aria-label={`${label}角色名`} placeholder="角色名" maxLength={40}
            value={block.character} onChange={(event) => onChange({ character: event.target.value })} />
          {text}
        </>
      ) : text}
      {selection && !suggestion && (
        <div role="toolbar" aria-label="选中文字操作" title="按 Tab 进入工具条" className="sc-selbar" style={{ top: selection.top, left: selection.left }} onMouseDown={keep}>
          <button type="button" onClick={() => void ask('rewrite')}>AI 改写</button>
          <button type="button" onClick={() => void ask('shorten')}>缩短</button>
          <span className="sc-selbar__tone">
            <button type="button" aria-haspopup="menu" aria-expanded={toneOpen} onClick={() => setToneOpen((open) => !open)}>换语气 ▾</button>
            {toneOpen && (
              <span role="menu" aria-label="换语气" className="sc-selbar__menu">
                {TONES.map((tone) => <button key={tone} type="button" role="menuitem" onClick={() => void ask('tone', tone)}>{tone}</button>)}
              </span>
            )}
          </span>
          <span className="sc-selbar__note">会调用 AI</span>
        </div>
      )}
      {suggestion && (
        <div className="sc-suggest" role="group" aria-label="AI 建议">
          <div className="sc-suggest__title">建议</div>
          <p className="sc-suggest__old">{suggestion.selection.text}</p>
          {suggestion.status === 'loading'
            ? <p className="sc-muted"><Loader2 size={13} className="sc-spin" aria-hidden="true" /> AI 正在改写…</p>
            : <p className="sc-suggest__new">{suggestion.text}</p>}
          <div className="sc-suggest__actions">
            <button type="button" className="sc-btn sc-btn--primary" disabled={suggestion.status !== 'ready'} onClick={accept}>替换</button>
            <button type="button" className="sc-btn" onClick={() => setSuggestion(null)}>取消</button>
          </div>
        </div>
      )}
    </div>
  )
}

interface Props {
  canvasId: string
  scene: ScriptScene
  number: number
  active: boolean
  onFocus: () => void
  onPatch: (patch: Partial<ScriptScene>) => void
  onBlocks: (fn: (blocks: ScriptBlock[]) => ScriptBlock[]) => void
}

/** 一场的卡片：标题、heading、块列表；块之间可插入动作 / 对白，块可上下移动、删除。 */
export default function SceneCard({ canvasId, scene, number, active, onFocus, onPatch, onBlocks }: Props) {
  const total = scene.blocks.length
  const insertRow = (after: number) => (
    <div className="sc-insert" role="group" aria-label={after < 0 ? `场 ${number} 添加块` : `场 ${number} 在块 ${after + 1} 后添加`}>
      <button type="button" disabled={total >= MAX_BLOCKS} onClick={() => onBlocks((blocks) => insertBlock(blocks, after, 'action'))}>+ 动作</button>
      <button type="button" disabled={total >= MAX_BLOCKS} onClick={() => onBlocks((blocks) => insertBlock(blocks, after, 'dialogue'))}>+ 对白</button>
    </div>
  )
  return (
    <section id={`scene-${scene.id}`} className={`sc-scene${active ? ' sc-scene--on' : ''}`} aria-label={`场 ${number}`} onFocusCapture={onFocus}>
      <header className="sc-scene__head">
        <span className="sc-scene__no">场 {number}</span>
        <input className="sc-scene__title" aria-label={`场 ${number} 标题`} placeholder="场景标题，如：雪夜竹林" maxLength={100}
          value={scene.title} onChange={(event) => onPatch({ title: event.target.value })} />
        <span className="sc-muted">约 {sceneSeconds(scene)} 秒</span>
      </header>
      <input className="sc-scene__heading" aria-label={`场 ${number} 场景说明`} placeholder="外景 · 夜 · 大雪" maxLength={100}
        value={scene.heading} onChange={(event) => onPatch({ heading: event.target.value })} />
      {total === 0 && insertRow(-1)}
      {scene.blocks.map((block, index) => (
        <div key={block.id || index} className="sc-row">
          <div className="sc-row__main">
            <BlockEditor canvasId={canvasId} block={block} label={`场 ${number} 块 ${index + 1} ${block.type === 'dialogue' ? '对白' : '动作'}`}
              onChange={(patch) => onBlocks((blocks) => blocks.map((item, i) => i === index ? { ...item, ...patch } : item))} />
            <span className="sc-row__tools">
              <button type="button" className="sc-icon" aria-label={`上移场 ${number} 块 ${index + 1}`} disabled={index === 0} onClick={() => onBlocks((blocks) => moveBlock(blocks, index, -1))}><ArrowUp size={13} aria-hidden="true" /></button>
              <button type="button" className="sc-icon" aria-label={`下移场 ${number} 块 ${index + 1}`} disabled={index === total - 1} onClick={() => onBlocks((blocks) => moveBlock(blocks, index, 1))}><ArrowDown size={13} aria-hidden="true" /></button>
              <button type="button" className="sc-icon" aria-label={`删除场 ${number} 块 ${index + 1}`} onClick={() => onBlocks((blocks) => blocks.filter((_, i) => i !== index))}><Trash2 size={13} aria-hidden="true" /></button>
            </span>
          </div>
          {insertRow(index)}
        </div>
      ))}
    </section>
  )
}
