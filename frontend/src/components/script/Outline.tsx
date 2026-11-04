import { ArrowDown, ArrowUp, Trash2 } from 'lucide-react'
import type { ScriptPayload } from '../../api/script'
import { MAX_ACTS, MAX_SCENES, allScenes, canMoveScene, sceneCount, sceneSeconds, totalSeconds } from './model'

interface Props {
  payload: ScriptPayload
  activeId: string | null
  open: boolean
  onToggle: () => void
  onSelect: (sceneId: string) => void
  onAddScene: () => void
  onAddAct: () => void
  onRenameAct: (actIndex: number, title: string) => void
  onRemoveAct: (actIndex: number) => void
  onMoveScene: (sceneId: string, direction: -1 | 1) => void
  onDeleteScene: (sceneId: string) => void
}

/** 左侧大纲：幕（可改名）→ 场列表，点击滚动到正文并高亮；窄屏收成顶部可展开的下拉。 */
export default function Outline({ payload, activeId, open, onToggle, onSelect, onAddScene, onAddAct, onRenameAct, onRemoveAct, onMoveScene, onDeleteScene }: Props) {
  const total = sceneCount(payload)
  const summary = `${total} 场 · 约 ${totalSeconds(payload)} 秒`
  const numbers = new Map(allScenes(payload).map((scene, index) => [scene.id, index + 1]))
  return (
    <aside className={`sc-outline${open ? ' sc-outline--open' : ''}`} aria-label="大纲">
      <button type="button" className="sc-outline__toggle" aria-expanded={open} onClick={onToggle}>
        <span>大纲</span><span className="sc-muted">{summary}</span>
      </button>
      <div className="sc-outline__panel">
        <div className="sc-outline__head"><h2>大纲</h2><span className="sc-muted">{summary}</span></div>
        {payload.acts.map((act, actIndex) => (
          <div key={actIndex} className="sc-outline__act">
            <div className="sc-outline__act-head">
              <input className="sc-outline__act-title" aria-label={`第 ${actIndex + 1} 幕标题`} value={act.title} placeholder="幕标题" maxLength={100}
                onChange={(event) => onRenameAct(actIndex, event.target.value)} />
              {act.scenes.length === 0 && (
                <button type="button" className="sc-icon" aria-label={`删除第 ${actIndex + 1} 幕`} onClick={() => onRemoveAct(actIndex)}><Trash2 size={13} aria-hidden="true" /></button>
              )}
            </div>
            {act.scenes.length === 0 && <p className="sc-muted sc-outline__none">这一幕还没有场</p>}
            {act.scenes.map((scene) => {
              const number = numbers.get(scene.id) ?? 0
              const name = `场 ${number}${scene.title.trim() ? ` · ${scene.title.trim()}` : ''}`
              return (
                <div key={scene.id} className={`sc-outline__scene${activeId === scene.id ? ' sc-outline__scene--on' : ''}`}>
                  <button type="button" className="sc-outline__link" aria-current={activeId === scene.id ? 'true' : undefined} onClick={() => onSelect(scene.id)}>
                    {name}<span className="sc-outline__secs">{sceneSeconds(scene)} 秒</span>
                  </button>
                  <span className="sc-outline__tools">
                    <button type="button" className="sc-icon" aria-label={`上移场 ${number}`} disabled={!canMoveScene(payload, scene.id, -1)} onClick={() => onMoveScene(scene.id, -1)}><ArrowUp size={13} aria-hidden="true" /></button>
                    <button type="button" className="sc-icon" aria-label={`下移场 ${number}`} disabled={!canMoveScene(payload, scene.id, 1)} onClick={() => onMoveScene(scene.id, 1)}><ArrowDown size={13} aria-hidden="true" /></button>
                    <button type="button" className="sc-icon" aria-label={`删除场 ${number}`} onClick={() => onDeleteScene(scene.id)}><Trash2 size={13} aria-hidden="true" /></button>
                  </span>
                </div>
              )
            })}
          </div>
        ))}
        <button type="button" className="sc-dashed" disabled={total >= MAX_SCENES} onClick={onAddScene}>+ 添加一场</button>
        <button type="button" className="sc-dashed" disabled={payload.acts.length >= MAX_ACTS || total >= MAX_SCENES} onClick={onAddAct}>+ 添加一幕</button>
      </div>
    </aside>
  )
}
