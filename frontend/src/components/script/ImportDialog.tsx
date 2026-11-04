import { useState } from 'react'
import { Loader2, X } from 'lucide-react'
import { scriptApi, type ScriptPayload } from '../../api/script'
import { toast } from '../ui/Toast'
import { useModal } from './useModal'
import { allScenes, dialogueCount, sceneCount } from './model'

const MAX_IMPORT = 50_000

interface Props {
  canvasId: string
  /** 当前已有内容时，替换前要二次确认 */
  onReplace: (payload: ScriptPayload) => Promise<boolean>
  onClose: () => void
}

/** 粘贴已有剧本：规则解析（不调用 AI）→ 预览幕 / 场 / 对白数量与前几场 → 确认后替换。 */
export default function ImportDialog({ canvasId, onReplace, onClose }: Props) {
  const [text, setText] = useState('')
  const [parsed, setParsed] = useState<ScriptPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const modal = useModal<HTMLDivElement>(onClose)

  const parse = async () => {
    setBusy(true); setError('')
    try {
      setParsed((await scriptApi.importText(canvasId, text)).payload)
    } catch (reason) {
      setParsed(null)
      setError(reason instanceof Error ? reason.message : '解析失败')
    } finally { setBusy(false) }
  }

  const replace = async () => {
    if (!parsed) return
    setBusy(true)
    try {
      if (await onReplace(parsed)) onClose()
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : '替换失败', 'error')
    } finally { setBusy(false) }
  }

  const scenes = parsed ? allScenes(parsed) : []
  return (
    <div className="sc-overlay" onClick={onClose}>
      <div className="sc-dialog" role="dialog" aria-modal="true" aria-label="粘贴已有剧本" ref={modal.ref} onKeyDown={modal.onKeyDown} onClick={(event) => event.stopPropagation()}>
        <header className="sc-drawer__head"><strong>粘贴已有剧本</strong><button type="button" className="sc-icon" aria-label="关闭粘贴剧本" onClick={onClose}><X size={14} aria-hidden="true" /></button></header>
        <p className="sc-muted">按“第X幕”“场 N / 第N场”“INT./EXT.”“名字：台词”等格式自动分幕分场，不会调用 AI。</p>
        <textarea className="sc-import" aria-label="剧本文本" rows={10} maxLength={MAX_IMPORT} value={text} placeholder="把已有剧本粘贴到这里"
          onChange={(event) => { setText(event.target.value); setParsed(null) }} />
        <div className="sc-dialog__row">
          <span className="sc-muted">{text.length.toLocaleString('en-US')} / {MAX_IMPORT.toLocaleString('en-US')} 字</span>
          <button type="button" className="sc-btn" disabled={busy || !text.trim()} onClick={() => void parse()}>
            {busy && !parsed && <Loader2 size={12} className="sc-spin" aria-hidden="true" />}解析预览
          </button>
        </div>
        {error && <div className="sc-error" role="alert">{error}</div>}
        {parsed && (
          <div className="sc-parsed" aria-label="解析结果">
            <p><strong>{parsed.acts.length} 幕 · {sceneCount(parsed)} 场 · {dialogueCount(parsed)} 句对白</strong></p>
            <ol>
              {scenes.slice(0, 3).map((scene, index) => (
                <li key={scene.id}>场 {index + 1}{scene.title ? ` · ${scene.title}` : ''}{scene.heading ? `（${scene.heading}）` : ''}：{scene.blocks[0]?.text.slice(0, 30) ?? ''}</li>
              ))}
            </ol>
            {scenes.length > 3 && <p className="sc-muted">…还有 {scenes.length - 3} 场</p>}
            {scenes.length === 0 && <p className="sc-muted">没有解析出内容</p>}
          </div>
        )}
        <div className="sc-dialog__actions">
          <button type="button" className="sc-btn" onClick={onClose}>取消</button>
          <button type="button" className="sc-btn sc-btn--primary" disabled={busy || !parsed || scenes.length === 0} onClick={() => void replace()}>用这份剧本替换</button>
        </div>
      </div>
    </div>
  )
}
