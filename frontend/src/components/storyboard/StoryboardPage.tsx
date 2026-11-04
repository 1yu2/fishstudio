import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { paths } from '../../routePaths'
import { BatchDialog } from '../nodeCanvas/BatchDialog'
import { layoutSplit } from '../nodeCanvas/graph'
import type { ProjectOutletContext } from '../project/outletContext'
import { toast } from '../ui/Toast'
import ShotFields from './ShotFields'
import ShotMedia from './ShotMedia'
import StoryStrip from './StoryStrip'
import { buildShots, newShot, reorderPatch } from './shots'
import { useStoryboard, type Storyboard } from './useStoryboard'
import './storyboard.css'

const DEFAULT_SPLIT_SHOTS = 6

/** 没有镜头时：说明 + 拆分镜头（取画布里第一个有文字的文本节点作为想法，没有就给文本框）。 */
function EmptyState({ sb, onAdd }: { sb: Storyboard; onAdd: () => void }) {
  const existingIdea = useMemo(() => {
    const node = sb.nodes.find((item) => item.type === 'text' && String((item.data as { text?: string }).text || '').trim())
    return node ? String((node.data as { text?: string }).text).trim() : ''
  }, [sb.nodes])
  const [idea, setIdea] = useState('')
  const [count, setCount] = useState(DEFAULT_SPLIT_SHOTS)
  const [busy, setBusy] = useState(false)
  const text = existingIdea || idea.trim()
  const run = async () => {
    if (!text || busy) return
    setBusy(true)
    try {
      const result = await sb.split(text, count)
      sb.update((current) => {
        const laid = layoutSplit(result, current.nodes)
        return { nodes: [...current.nodes, ...laid.nodes], edges: [...current.edges, ...laid.edges] }
      })
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : '拆分失败', 'error')
    } finally { setBusy(false) }
  }
  return (
    <section className="sb-empty" aria-label="还没有镜头">
      <h2>还没有镜头</h2>
      <p>把一个想法拆成角色和镜头，之后在这里逐个镜头生成画面、修改、转成视频。也可以先到画布里自己搭。</p>
      {existingIdea
        ? <p className="sb-empty__idea">想法（取自画布里的文本节点）：{existingIdea}</p>
        : <textarea aria-label="想法" rows={4} value={idea} onChange={(event) => setIdea(event.target.value)} placeholder="例如：雪夜竹林，狐妖回头看向书生……" />}
      <label className="sb-inline">镜头数
        <input type="number" min={1} max={12} value={count} onChange={(event) => setCount(Math.max(1, Math.min(12, Number(event.target.value) || 1)))} />
      </label>
      <button type="button" className="sb-btn sb-btn--primary" disabled={!text || busy} onClick={() => void run()}>
        {busy ? <Loader2 size={13} className="sb-spin" aria-hidden="true" /> : <Sparkles size={13} aria-hidden="true" />}
        拆分镜头（会调用 AI）
      </button>
      <div className="sb-empty__links">
        <button type="button" className="sb-link" onClick={onAdd}>手动添加一个镜头</button>
        <Link className="sb-link" to={paths.projectCanvas(sb.canvasId)}>到画布里自己搭</Link>
      </div>
    </section>
  )
}

/** 第 3 步：分镜页。和画布是同一份数据的两种视图（读写画布 graph 的节点 data 与画布的 runs）。 */
export default function StoryboardPage() {
  const { canvasId = '' } = useParams()
  const outlet = useOutletContext<ProjectOutletContext | null>()
  const refreshSummary = outlet?.refreshSummary
  const sb = useStoryboard(canvasId)
  const shots = useMemo(() => buildShots(sb.nodes, sb.edges, sb.runs), [sb.nodes, sb.edges, sb.runs])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = shots.find((shot) => shot.id === selectedId) ?? shots[0]

  // 保存完成或结果变化后，让项目页的步骤条 / 底栏（镜头完成数）尽快更新，不等 10 秒轮询
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; return }
    refreshSummary?.()
  }, [sb.revision, sb.signature, refreshSummary])

  const move = (from: number, to: number) => {
    const patch = reorderPatch(shots, from, to)
    if (Object.keys(patch).length === 0) return
    setSelectedId(selected?.id ?? null)  // 排序后继续选中原来的镜头，不跟着位置跳
    sb.update((current) => ({ ...current, nodes: current.nodes.map((node) => node.id in patch ? { ...node, data: { ...node.data, order: patch[node.id] } } : node) }))
  }

  const add = () => {
    const created = newShot(sb.nodes, shots)
    sb.update((current) => ({
      nodes: [
        ...current.nodes.map((node) => node.id in created.orders ? { ...node, data: { ...node.data, order: created.orders[node.id] } } : node),
        ...created.nodes,
      ],
      edges: [...current.edges, ...created.edges],
    }))
    setSelectedId(created.mainId)
  }

  if (sb.error) return <div className="sb-message" role="alert">{sb.error}</div>
  if (!sb.loaded) return <div className="sb-message" aria-busy="true">分镜加载中…</div>
  return (
    <div className="sb">
      {shots.length === 0 ? <EmptyState sb={sb} onAdd={add} /> : (
        <>
          <StoryStrip
            shots={shots} selectedId={selected?.id} estimates={sb.estimates} batch={sb.batch} hintDismissed={sb.hintDismissed}
            noVideoModel={!!sb.models && !sb.models.defaults.video_preview && !sb.models.defaults.video_final}
            onSelect={setSelectedId} onMove={move} onAdd={add} onBatch={(kind) => void sb.openBatch([kind])}
            onCancelBatch={() => void sb.cancelBatch()} onDismissHint={sb.setHintDismissed}
          />
          {selected && (
            <div className="sb-detail">
              <ShotMedia key={selected.id} shot={selected} sb={sb} />
              <ShotFields shot={selected} total={shots.length} canvasId={canvasId} sb={sb} onMove={move} />
            </div>
          )}
        </>
      )}
      {sb.plan && (
        <div className="sb-drawer">
          <BatchDialog plan={sb.plan} onConfirm={() => void sb.confirmBatch()} onClose={() => sb.setPlan(null)} />
        </div>
      )}
    </div>
  )
}
