import { useEffect, useRef, useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { Link } from 'react-router-dom'
import { scriptApi, type ScriptPayload } from '../../api/script'
import { paths } from '../../routePaths'
import { toast } from '../ui/Toast'
import { allScenes, sceneCount } from './model'

interface Props {
  canvasId: string
  /** 取自画布里第一个有文字的文本节点 */
  defaultIdea: string
  onUseDraft: (payload: ScriptPayload) => Promise<void>
  onImport: () => void
  onBlank: () => void
}

/** 没有剧本时的三个入口：AI 起草 / 粘贴已有剧本 / 从空白开始，另有“直接拆镜头”小链接。 */
export default function EmptyState({ canvasId, defaultIdea, onUseDraft, onImport, onBlank }: Props) {
  const [idea, setIdea] = useState(defaultIdea)
  const typed = useRef(false)
  // 画布加载晚于挂载时，想法到了再填；用户已经输入过就不覆盖
  useEffect(() => { if (!typed.current) setIdea(defaultIdea) }, [defaultIdea])
  const [scenes, setScenes] = useState(4)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<ScriptPayload | null>(null)

  const write = async () => {
    if (!idea.trim() || busy) return
    setBusy(true)
    try {
      setDraft((await scriptApi.draft(canvasId, idea.trim(), scenes)).payload)
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : 'AI 暂时不可用，请稍后再试', 'error')
    } finally { setBusy(false) }
  }

  const use = async () => {
    if (!draft) return
    setBusy(true)
    try { await onUseDraft(draft) } catch (reason) { toast(reason instanceof Error ? reason.message : '保存失败', 'error') } finally { setBusy(false) }
  }

  return (
    <section className="sc-empty" aria-label="还没有剧本">
      <h2>还没有剧本</h2>
      <p className="sc-muted">先有剧本，再拆出角色和分镜。选一种方式开始：</p>
      <div className="sc-empty__cards">
        <div className="sc-empty__card">
          <h3>用 AI 写剧本</h3>
          <textarea aria-label="剧本想法" rows={4} value={idea} placeholder="例如：雪夜竹林，狐妖等了书生三百年……" onChange={(event) => { typed.current = true; setIdea(event.target.value); setDraft(null) }} />
          <label className="sc-inline">场数
            <input type="number" min={2} max={8} value={scenes} onChange={(event) => { setScenes(Math.max(2, Math.min(8, Number(event.target.value) || 2))); setDraft(null) }} />
          </label>
          {!draft ? (
            <button type="button" className="sc-btn sc-btn--primary" disabled={!idea.trim() || busy} onClick={() => void write()}>
              {busy ? <Loader2 size={13} className="sc-spin" aria-hidden="true" /> : <Sparkles size={13} aria-hidden="true" />}
              用 AI 写剧本（会调用 AI）
            </button>
          ) : (
            <div className="sc-draft" aria-label="AI 起草预览">
              <p><strong>{draft.acts.length} 幕 · {sceneCount(draft)} 场</strong></p>
              <ol>{allScenes(draft).map((scene, index) => <li key={scene.id}>场 {index + 1}{scene.title ? ` · ${scene.title}` : ''}：{scene.blocks[0]?.text.slice(0, 30)}</li>)}</ol>
              <div className="sc-dialog__actions">
                <button type="button" className="sc-btn" disabled={busy} onClick={() => void write()}>重新生成（会调用 AI）</button>
                <button type="button" className="sc-btn sc-btn--primary" disabled={busy} onClick={() => void use()}>使用这份剧本</button>
              </div>
            </div>
          )}
        </div>
        <div className="sc-empty__card">
          <h3>粘贴已有剧本</h3>
          <p className="sc-muted">把写好的剧本粘贴进来，按格式自动分幕分场，不调用 AI。</p>
          <button type="button" className="sc-btn" onClick={onImport}>粘贴已有剧本</button>
        </div>
        <div className="sc-empty__card">
          <h3>自己从空白开始</h3>
          <p className="sc-muted">建一幕一场，直接动手写。</p>
          <button type="button" className="sc-btn" onClick={onBlank}>自己从空白开始</button>
        </div>
      </div>
      <Link className="sc-link" to={paths.projectStep(canvasId, 'storyboard')}>直接拆镜头 →</Link>
    </section>
  )
}
