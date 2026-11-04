import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Loader2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { Quote, RunRequest, StoredNode } from '../../api/nodeCanvas'
import { paths } from '../../routePaths'
import { resolveInputs } from '../nodeCanvas/graph'
import { CAMERA_MOVES, DURATIONS, SHOT_SIZES, priceSuffix, type Shot } from './shots'
import type { Storyboard } from './useStoryboard'

/** 单节点报价：请求变化后防抖取价；有 blockers 时不报价。 */
function useNodeQuote(quote: Storyboard['quote'], request: RunRequest, enabled: boolean): { price: Quote | null; error: string } {
  const [state, setState] = useState<{ price: Quote | null; error: string }>({ price: null, error: '' })
  const key = JSON.stringify(request)
  useEffect(() => {
    if (!enabled) { setState({ price: null, error: '' }); return }
    let cancelled = false
    const timer = window.setTimeout(() => {
      quote(JSON.parse(key) as RunRequest)
        .then((value) => { if (!cancelled) setState({ price: value, error: '' }) })
        .catch((reason) => { if (!cancelled) setState({ price: null, error: reason instanceof Error && reason.message ? reason.message : '暂时无法报价' }) })
    }, 600)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [enabled, key, quote])
  return state
}

const CUSTOM = '__custom'

function Generate({ node, label, primary, running, sb, extraBlocker }: {
  node: StoredNode; label: string; primary?: boolean; running: boolean; sb: Storyboard; extraBlocker?: string
}) {
  const resolved = resolveInputs(node, sb.nodes, sb.edges, sb.runs, sb.aspectRatio)
  const blockers = extraBlocker ? [extraBlocker, ...resolved.blockers] : resolved.blockers
  const { price, error } = useNodeQuote(sb.quote, resolved.request, blockers.length === 0)
  // 报价失败（如没有配置视频模型）时按钮禁用，原因显示在按钮下方，和 blockers 同样处理
  const reasons = error ? [...blockers, error] : blockers
  const [busy, setBusy] = useState(false)
  const start = async () => { setBusy(true); try { await sb.run(node.id, resolved.request) } finally { setBusy(false) } }
  return (
    <>
      <button type="button" className={`sb-btn${primary ? ' sb-btn--primary' : ''}`} disabled={busy || running || reasons.length > 0}
        title={reasons[0]} onClick={() => void start()}>
        {(busy || running) && <Loader2 size={12} className="sb-spin" aria-hidden="true" />}
        {running ? '生成中…' : `${label}${priceSuffix(price)}`}
      </button>
      {reasons.length > 0 && <ul className="sb-blockers">{reasons.map((text) => <li key={text}>{text}</li>)}</ul>}
    </>
  )
}

interface Props {
  shot: Shot
  total: number
  canvasId: string
  sb: Storyboard
  onMove: (from: number, to: number) => void
}

/** 镜头详情右侧：可编辑字段（写回画布节点 data）+ 生成操作。 */
export default function ShotFields({ shot, total, canvasId, sb, onMove }: Props) {
  const { frame, video } = shot
  const describeNode = frame ?? video
  const cameraCustom = !!shot.camera && !(CAMERA_MOVES as readonly string[]).includes(shot.camera)
  const [customOpen, setCustomOpen] = useState(cameraCustom)
  useEffect(() => { setCustomOpen(cameraCustom) }, [shot.id, cameraCustom])
  const cameraValue = customOpen || cameraCustom ? CUSTOM : shot.camera
  const imageProblem = frame && !String((frame.data as { model?: string }).model || '') && sb.models?.image_problem ? sb.models.image_problem : ''
  return (
    <div className="sb-fields">
      <div className="sb-fields__head">
        <h2>镜头 {shot.index + 1}{shot.title && !/^镜头 \d+$/.test(shot.title) ? ` · ${shot.title}` : ''}</h2>
        <span className="sb-fields__move">
          <button type="button" className="sb-icon" aria-label="左移" disabled={shot.index === 0} onClick={() => onMove(shot.index, shot.index - 1)}><ChevronLeft size={16} aria-hidden="true" /></button>
          <button type="button" className="sb-icon" aria-label="右移" disabled={shot.index === total - 1} onClick={() => onMove(shot.index, shot.index + 1)}><ChevronRight size={16} aria-hidden="true" /></button>
        </span>
      </div>
      <label className="sb-field">画面描述
        <textarea rows={3} value={shot.description} onChange={(event) => describeNode && sb.updateData(describeNode.id, { prompt: event.target.value })} />
      </label>
      <div className="sb-grid">
        <label className="sb-field">景别
          <select value={shot.shotSize} disabled={!frame} onChange={(event) => frame && sb.updateData(frame.id, { shot_size: event.target.value })}>
            <option value="">未指定</option>
            {SHOT_SIZES.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label className="sb-field">镜头运动
          <select value={cameraValue} disabled={!video}
            onChange={(event) => {
              if (!video) return
              if (event.target.value === CUSTOM) { setCustomOpen(true); return }
              setCustomOpen(false)
              sb.updateData(video.id, { camera: event.target.value })
            }}>
            <option value="">未指定</option>
            {CAMERA_MOVES.map((item) => <option key={item} value={item}>{item}</option>)}
            <option value={CUSTOM}>自定义…</option>
          </select>
        </label>
        <label className="sb-field">时长
          <select value={shot.duration} disabled={!video} onChange={(event) => video && sb.updateData(video.id, { duration: Number(event.target.value) })}>
            {DURATIONS.map((item) => <option key={item} value={item}>{item} 秒</option>)}
          </select>
        </label>
      </div>
      {cameraValue === CUSTOM && video && (
        <label className="sb-field">自定义运镜
          <input type="text" value={shot.camera} maxLength={40} placeholder="例如：从肩后缓缓绕到正面"
            onChange={(event) => sb.updateData(video.id, { camera: event.target.value })} />
        </label>
      )}
      <div className="sb-field sb-field--muted" aria-disabled="true">出镜角色
        <span className="sb-muted">第 4 步上线后可编辑</span>
      </div>
      <div className="sb-field sb-field--muted" aria-disabled="true">台词
        <span className="sb-muted">第 6 步上线后可编辑</span>
      </div>
      <div className="sb-actions">
        {frame && <Generate node={frame} label="重新生成图片" running={shot.frameStatus.state === 'running'} sb={sb} extraBlocker={imageProblem} />}
        {video && <Generate node={video} label="生成视频" primary running={shot.videoStatus.state === 'running'} sb={sb} />}
        <Link className="sb-link sb-link--open" to={`${paths.projectCanvas(canvasId)}?node=${encodeURIComponent(shot.id)}`}>在画布中打开</Link>
      </div>
    </div>
  )
}
