import { useState } from 'react'
import { Loader2, Plus, X } from 'lucide-react'
import type { BatchKind, BatchRun } from '../../api/nodeCanvas'
import { ProtectedImage } from '../common/ProtectedMedia'
import { kindsLabel, money } from '../nodeCanvas/BatchDialog'
import type { Estimate } from './useStoryboard'
import { totalSeconds, type Shot } from './shots'

const BATCH_LABEL: Record<BatchKind, string> = { image: '生成剩余图片', video: '全部转视频' }

/** 批量按钮：带预取的预估价；没有可运行的节点时禁用并写明原因。 */
function batchButton(kind: BatchKind, estimate: Estimate, running: boolean, empty: boolean, noVideoModel: boolean) {
  const base = BATCH_LABEL[kind]
  if (empty) return { label: base, disabled: true, reason: '还没有镜头', short: '还没有镜头' }
  if (kind === 'video' && noVideoModel) return { label: base, disabled: true, reason: '还没有设置视频模型：在「设置 → 模型服务 → 默认模型」里设置「镜头视频」', short: '还没有设置视频模型' }
  if (running) return { label: base, disabled: true, reason: '有批量任务正在运行', short: '批量任务运行中' }
  const quote = estimate.state === 'ready' ? estimate.quote : null
  if (!quote) return { label: base, disabled: false, short: '', reason: estimate.state === 'error' ? '暂时无法预估价格，点击后会重新报价' : '正在预估价格…' }
  const runnable = quote.runnable_count ?? quote.nodes.filter((item) => item.status !== 'skipped').length
  if (runnable === 0) {
    const waiting = quote.waiting_count ?? 0
    const reason = kind === 'image' ? '所有镜头都已有画面' : waiting > 0 ? '镜头的画面还没生成，先生成画面' : '所有镜头都已有视频'
    const short = kind === 'image' ? '画面都已生成' : waiting > 0 ? '先生成画面' : (quote.unavailable_nodes.length > 0 ? '没有可以运行的节点' : '视频都已生成')
    return { label: base, disabled: true, reason, short }
  }
  return { label: `${base} · 预估 ${money(quote.estimated_total)}`, disabled: false, reason: '', short: '' }
}

function Thumb({ shot }: { shot: Shot }) {
  const progress = shot.state === 'running' ? (shot.videoStatus.state === 'running' ? shot.videoStatus : shot.frameStatus).progress : 0
  return (
    <span className={`sb-card__thumb sb-card__thumb--${shot.state}`}>
      {shot.thumbnail ? <ProtectedImage src={shot.thumbnail.url} alt="" /> : null}
      {shot.state === 'failed' && <span className="sb-card__bang" aria-hidden="true">!</span>}
      {shot.state === 'running' && (
        <span className="sb-card__bar" aria-hidden="true"><span style={{ width: `${progress}%` }} /></span>
      )}
    </span>
  )
}

interface StripProps {
  shots: Shot[]
  selectedId: string | undefined
  estimates: Record<BatchKind, Estimate>
  batch: BatchRun | null
  hintDismissed: string | null
  noVideoModel: boolean
  onSelect: (id: string) => void
  onMove: (from: number, to: number) => void
  onAdd: () => void
  onBatch: (kind: BatchKind) => void
  onCancelBatch: () => void
  onDismissHint: (batchId: string) => void
}

/** 顶部故事板横条：统计 + 批量按钮 + 可拖动排序的缩略图卡片。 */
export default function StoryStrip(props: StripProps) {
  const { shots, selectedId, estimates, batch, hintDismissed } = props
  const [dragFrom, setDragFrom] = useState<number | null>(null)
  const [dragOver, setDragOver] = useState<number | null>(null)
  const seconds = totalSeconds(shots)
  const batchRunning = batch?.status === 'running'
  const showHint = !!batch && batch.id !== hintDismissed && batch.status !== 'running' && batch.status !== 'canceled'
    && batch.kinds?.length === 1 && batch.kinds[0] === 'image'
  return (
    <section className="sb-strip" aria-label="故事板">
      <div className="sb-strip__head">
        <span className="sb-strip__stat">{shots.length} 个镜头 · 约 {seconds} 秒 · 拖动可调整顺序</span>
        <div className="sb-strip__actions">
          {(['image', 'video'] as const).map((kind) => {
            const button = batchButton(kind, estimates[kind], batchRunning, shots.length === 0, props.noVideoModel)
            return (
              <div key={kind} className="sb-strip__action">
                <button type="button" className="sb-btn" disabled={button.disabled} title={button.reason || undefined} onClick={() => props.onBatch(kind)}>
                  {button.label}
                </button>
                {button.disabled && button.short && <span className="sb-strip__reason">{button.short}</span>}
              </div>
            )
          })}
        </div>
      </div>
      {batch && (
        <div className="sb-strip__batch" role="status">
          {batch.status === 'running' && <Loader2 size={12} className="sb-spin" aria-hidden="true" />}
          {kindsLabel(batch.kinds)} {batch.completed}/{batch.total} 完成{batch.failed > 0 ? ` · ${batch.failed} 失败` : ''}{batch.status === 'canceled' ? ' · 已取消' : ''}
          {batch.status === 'running' && <button type="button" className="sb-link" onClick={props.onCancelBatch}>取消</button>}
        </div>
      )}
      {showHint && batch && (
        <div className="sb-strip__hint" role="status">
          画面已完成：挑选满意的图，再点「全部转视频」
          <button type="button" className="sb-link" onClick={() => props.onBatch('video')}>全部转视频</button>
          <button type="button" className="sb-icon" aria-label="关闭提示" onClick={() => props.onDismissHint(batch.id)}><X size={12} /></button>
        </div>
      )}
      <ol className="sb-strip__list">
        {shots.map((shot, index) => (
          <li key={shot.id}>
            <button
              type="button"
              className={`sb-card${shot.id === selectedId ? ' sb-card--on' : ''}${dragOver === index && dragFrom !== index ? ' sb-card--over' : ''}`}
              aria-current={shot.id === selectedId ? 'true' : undefined}
              aria-label={`镜头 ${index + 1}：${shot.caption}`}
              draggable
              onClick={() => props.onSelect(shot.id)}
              onDragStart={(event) => { setDragFrom(index); event.dataTransfer?.setData('text/plain', shot.id) }}
              onDragOver={(event) => { event.preventDefault(); setDragOver(index) }}
              onDragLeave={() => setDragOver((value) => (value === index ? null : value))}
              onDrop={(event) => {
                event.preventDefault()
                if (dragFrom !== null && dragFrom !== index) props.onMove(dragFrom, index)
                setDragFrom(null); setDragOver(null)
              }}
              onDragEnd={() => { setDragFrom(null); setDragOver(null) }}
            >
              <Thumb shot={shot} />
              <span className={`sb-card__caption sb-card__caption--${shot.state}`}>{index + 1} · {shot.caption}</span>
            </button>
          </li>
        ))}
        <li>
          <button type="button" className="sb-add" aria-label="添加镜头" onClick={props.onAdd}><Plus size={18} aria-hidden="true" /></button>
        </li>
      </ol>
    </section>
  )
}
