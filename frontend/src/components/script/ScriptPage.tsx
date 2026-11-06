import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useOutletContext, useParams } from 'react-router-dom'
import { ConflictError, nodeCanvasApi, type StoredEdge, type StoredNode } from '../../api/nodeCanvas'
import type { ScriptPayload } from '../../api/script'
import { paths } from '../../routePaths'
import { layoutSplit } from '../nodeCanvas/graph'
import type { ProjectOutletContext } from '../project/outletContext'
import { buildShots } from '../storyboard/shots'
import { confirmDialog } from '../ui/ConfirmDialog'
import { toast } from '../ui/Toast'
import EmptyState from './EmptyState'
import HistoryDrawer from './HistoryDrawer'
import ImportDialog from './ImportDialog'
import Outline from './Outline'
import SceneCard, { CHARACTER_LIST_ID } from './SceneCard'
import {
  addAct, addScene, allScenes, blankScript, characterNames, flattenScript, hasContent, longDialogueHint, moveScene,
  removeAct, removeScene, sceneCount, SPLIT_IDEA_LIMIT, splitShotCount, updateAct, updateBlocks, updateScene,
} from './model'
import { useScript, type SaveState } from './useScript'
import './script.css'

const SAVE_TEXT: Record<SaveState, string> = { saved: '已保存', saving: '保存中…', dirty: '未保存', error: '保存失败' }
const CONFIRM_LABEL = '确认剧本，拆出角色和分镜 →'
/** 后端没有拆分报价接口（拆分走免费额度，不计入画布花费），所以只提示额度，不显示金额 */
const SPLIT_NOTE = '拆分免费额度内'
const DONE_LABEL = '剧本已确认，去定角色 →'

interface CanvasSnapshot { loaded: boolean; failed: boolean; nodes: StoredNode[]; edges: StoredEdge[] }

/** 第 1 步：剧本页。左侧大纲、右侧正文；剧本按版本保存，确认后拆成角色和镜头放进画布。 */
export default function ScriptPage() {
  const { canvasId = '' } = useParams()
  const navigate = useNavigate()
  const outlet = useOutletContext<ProjectOutletContext | null>()
  const refreshSummary = outlet?.refreshSummary
  const setFooter = outlet?.setFooter
  const script = useScript(canvasId)
  const { payload, update, replaceAll, flush, getPayload } = script

  const [canvas, setCanvas] = useState<CanvasSnapshot>({ loaded: false, failed: false, nodes: [], edges: [] })
  const [activeId, setActiveId] = useState<string | null>(null)
  const [outlineOpen, setOutlineOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [splitting, setSplitting] = useState(false)
  const [scrollTo, setScrollTo] = useState<string | null>(null)
  const started = useRef(false)  // 页面里创建过内容（从空白开始 / 起草 / 导入）后不再回到空状态

  useEffect(() => {
    let cancelled = false
    nodeCanvasApi.get(canvasId).then((value) => {
      if (!cancelled) setCanvas({ loaded: true, failed: false, nodes: value.graph.nodes ?? [], edges: value.graph.edges ?? [] })
    }).catch(() => { if (!cancelled) setCanvas({ loaded: true, failed: true, nodes: [], edges: [] }) })
    return () => { cancelled = true }
  }, [canvasId])

  // 保存成功（版本变化）后，让项目页步骤条 / 底栏尽快更新
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; return }
    refreshSummary?.()
  }, [script.revision, refreshSummary])

  useEffect(() => {
    if (!scrollTo) return
    document.getElementById(`scene-${scrollTo}`)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    setScrollTo(null)
  }, [scrollTo])

  const hasShots = useMemo(() => buildShots(canvas.nodes, canvas.edges, null).length > 0, [canvas.nodes, canvas.edges])
  const defaultIdea = useMemo(() => {
    const node = canvas.nodes.find((item) => item.type === 'text' && String((item.data as { text?: string }).text || '').trim())
    return node ? String((node.data as { text?: string }).text).trim() : ''
  }, [canvas.nodes])
  const scenes = useMemo(() => allScenes(payload), [payload])
  const names = useMemo(() => characterNames(payload), [payload])
  const content = hasContent(payload)
  const hint = useMemo(() => longDialogueHint(payload), [payload])
  const current = activeId && scenes.some((scene) => scene.id === activeId) ? activeId : scenes[0]?.id ?? null
  const showEmpty = script.revision === null && payload.acts.length === 0 && !started.current

  // 大纲随滚动高亮：观察每一场卡片，取视口上部区域里最靠前的一场
  const sceneKey = scenes.map((scene) => scene.id).join('|')
  const quietUntil = useRef(0)  // 点大纲触发的平滑滚动期间不跟随，避免高亮在途经的场之间跳
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined' || !sceneKey) return
    const ids = sceneKey.split('|')
    const visible = new Set<string>()
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const id = entry.target.id.replace(/^scene-/, '')
        if (entry.isIntersecting) visible.add(id); else visible.delete(id)
      }
      if (Date.now() < quietUntil.current) return
      const first = ids.find((id) => visible.has(id))
      if (first) setActiveId(first)
    }, { rootMargin: '-10% 0px -70% 0px' })
    for (const id of ids) {
      const el = document.getElementById(`scene-${id}`)
      if (el) observer.observe(el)
    }
    return () => observer.disconnect()
  }, [sceneKey, script.loaded])

  // ── 编辑 ──
  const select = (sceneId: string) => { quietUntil.current = Date.now() + 800; setActiveId(sceneId); setScrollTo(sceneId); setOutlineOpen(false) }
  const actOf = (sceneId: string | null) => Math.max(0, payload.acts.findIndex((act) => act.scenes.some((scene) => scene.id === sceneId)))

  const handleAddScene = () => {
    const created = addScene(payload, actOf(current))
    if (!created) { toast('场数已达上限（80 场）', 'error'); return }
    started.current = true
    update(() => created.payload)
    select(created.sceneId)
  }
  const handleAddAct = () => {
    const created = addAct(payload)
    if (!created) { toast('幕数或场数已达上限', 'error'); return }
    update(() => created.payload)
    select(created.sceneId)
  }
  const handleDeleteScene = async (sceneId: string) => {
    const number = scenes.findIndex((scene) => scene.id === sceneId) + 1
    if (!await confirmDialog({ title: `删除场 ${number}？`, description: '这一场的全部内容会被删除（可在历史版本里找回）。', confirmText: '删除', danger: true })) return
    update((value) => removeScene(value, sceneId))
  }

  const adoptPayload = async (next: ScriptPayload, source: 'ai_draft' | 'import') => {
    started.current = true
    await replaceAll(next, source)
    setActiveId(next.acts.flatMap((act) => act.scenes)[0]?.id ?? null)
  }
  /** 返回 false 表示用户在二次确认里取消 */
  const importReplace = async (next: ScriptPayload): Promise<boolean> => {
    if (content && !await confirmDialog({ title: '替换当前剧本？', description: '当前剧本会被这份剧本替换（旧版本保留在历史版本里）。', confirmText: '用这份剧本替换' })) return false
    await adoptPayload(next, 'import')
    return true
  }
  const startBlank = () => { started.current = true; const next = blankScript(); update(() => next); setActiveId(next.acts[0].scenes[0].id) }

  // ── 拆分镜头 ──
  /** 剧本拍平成文字 → 画布 copilot split → 追加进画布 graph（409 时重新读取再试一次）。 */
  const splitIntoCanvas = useCallback(async () => {
    await flush()
    // 保存完成后再读最新内容（点击时捕获的 payload 可能已过期）
    const latest = getPayload()
    const idea = flattenScript(latest)
    if (idea.length > SPLIT_IDEA_LIMIT) throw new Error(`剧本太长，超过 ${SPLIT_IDEA_LIMIT.toLocaleString('en-US')} 字，请分集或精简`)
    const result = await nodeCanvasApi.split(canvasId, idea, splitShotCount(sceneCount(latest)))
    for (let attempt = 0; ; attempt += 1) {
      const detail = await nodeCanvasApi.get(canvasId)
      const laid = layoutSplit(result, detail.graph.nodes ?? [])
      const nodes = [...(detail.graph.nodes ?? []), ...laid.nodes]
      const edges = [...(detail.graph.edges ?? []), ...laid.edges]
      try {
        await nodeCanvasApi.save(canvasId, { nodes, edges, ...(detail.graph.viewport ? { viewport: detail.graph.viewport } : {}) }, detail.revision)
        setCanvas({ loaded: true, failed: false, nodes, edges })
        break
      } catch (reason) {
        if (reason instanceof ConflictError && attempt === 0) continue
        throw reason
      }
    }
    refreshSummary?.()
  }, [canvasId, getPayload, flush, refreshSummary])

  const runSplit = useCallback(async (goNext: boolean) => {
    setSplitting(true)
    try {
      await splitIntoCanvas()
      toast('已按剧本拆出角色和镜头', 'success')
      if (goNext) navigate(paths.projectStep(canvasId, 'characters'))
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : '拆分失败', 'error')
    } finally { setSplitting(false) }
  }, [splitIntoCanvas, navigate, canvasId])

  const resplit = async () => {
    if (!await confirmDialog({ title: '按剧本重新拆分？', description: '会调用 AI，并在画布里追加新的角色和镜头，已有镜头不会被删除。', confirmText: '重新拆分' })) return
    await runSplit(false)
  }

  // ── 底栏（覆盖项目框架的主按钮）──
  const clickRef = useRef<() => void>(() => undefined)
  useEffect(() => {
    clickRef.current = () => {
      // 已有镜头：先保存剧本；保存失败就留在本页提示，不带着未保存的内容离开
      if (hasShots) flush().then(() => navigate(paths.projectStep(canvasId, 'characters'))).catch((reason) => toast(reason instanceof Error ? reason.message : '剧本保存失败', 'error'))
      else void runSplit(true)
    }
  }, [hasShots, flush, navigate, canvasId, runSplit])
  const ready = script.loaded && canvas.loaded
  const footerLabel = splitting ? '正在拆分…' : hasShots ? DONE_LABEL : CONFIRM_LABEL
  const footerNote = !ready ? '' : hasShots ? '' : canvas.failed ? '画布加载失败' : !content ? '先写剧本' : SPLIT_NOTE
  const footerDisabled = !ready || (!hasShots && (!content || canvas.failed))
  useEffect(() => {
    setFooter?.({ label: footerLabel, onClick: () => clickRef.current(), disabled: footerDisabled, busy: splitting, note: footerNote || undefined, hint: hint || undefined })
  }, [setFooter, footerLabel, footerDisabled, splitting, footerNote, hint])
  useEffect(() => () => setFooter?.(null), [setFooter])

  if (script.error) return <div className="sc-message" role="alert">{script.error}</div>
  if (!script.loaded) return <div className="sc-message" aria-busy="true">剧本加载中…</div>

  if (showEmpty) {
    return (
      <div className="sc">
        <EmptyState canvasId={canvasId} defaultIdea={defaultIdea} onUseDraft={(next) => adoptPayload(next, 'ai_draft')} onImport={() => setImportOpen(true)} onBlank={startBlank} />
        {importOpen && <ImportDialog canvasId={canvasId} onReplace={importReplace} onClose={() => setImportOpen(false)} />}
      </div>
    )
  }

  let number = 0
  return (
    <div className="sc">
      <div className="sc-body">
        <Outline
          payload={payload} activeId={current} open={outlineOpen} onToggle={() => setOutlineOpen((open) => !open)} onSelect={select}
          onAddScene={handleAddScene} onAddAct={handleAddAct}
          onRenameAct={(index, title) => update((value) => updateAct(value, index, { title }))}
          onRemoveAct={(index) => update((value) => removeAct(value, index))}
          onMoveScene={(id, direction) => update((value) => moveScene(value, id, direction))}
          onDeleteScene={(id) => void handleDeleteScene(id)}
        />
        <article className="sc-main" aria-label="剧本正文">
          <div className="sc-bar">
            <span role="status" className={`sc-status sc-status--${script.saveState}`}>
              {script.revision === null ? '尚未保存' : `版本 v${script.revision}`} · {SAVE_TEXT[script.saveState]} · {script.words.toLocaleString('en-US')} 字
            </span>
            <div className="sc-bar__actions">
              {hasShots && content && <button type="button" className="sc-btn" disabled={splitting} onClick={() => void resplit()}>按剧本重新拆分（追加镜头）</button>}
              <button type="button" className="sc-btn" onClick={() => setHistoryOpen(true)}>历史版本</button>
              <button type="button" className="sc-btn" onClick={() => setImportOpen(true)}>粘贴已有剧本</button>
            </div>
          </div>
          <datalist id={CHARACTER_LIST_ID}>{names.map((name) => <option key={name} value={name} />)}</datalist>
          <div className="sc-scenes">
            {payload.acts.map((act, actIndex) => (
              <div key={actIndex} className="sc-actblock">
                {act.title.trim() && <h2 className="sc-act">{act.title}</h2>}
                {act.scenes.map((scene) => {
                  number += 1
                  return (
                    <SceneCard key={scene.id} canvasId={canvasId} scene={scene} number={number} active={current === scene.id}
                      onFocus={() => setActiveId(scene.id)}
                      onPatch={(patch) => update((value) => updateScene(value, scene.id, patch))}
                      onBlocks={(fn) => update((value) => updateBlocks(value, scene.id, fn))} />
                  )
                })}
              </div>
            ))}
            {scenes.length === 0 && <p className="sc-muted">还没有场，点左侧“+ 添加一场”开始写。</p>}
            <Link className="sc-link" to={paths.projectStep(canvasId, 'storyboard')}>直接拆镜头 →</Link>
          </div>
        </article>
      </div>
      {historyOpen && (
        <HistoryDrawer canvasId={canvasId} revisions={script.revisions} current={script.revision} onRestore={script.restore} onClose={() => setHistoryOpen(false)} />
      )}
      {importOpen && <ImportDialog canvasId={canvasId} onReplace={importReplace} onClose={() => setImportOpen(false)} />}
    </div>
  )
}
