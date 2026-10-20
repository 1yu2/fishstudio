import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Film, ImageIcon, Loader2, Play, Type, Upload } from 'lucide-react'
import type { CanvasModel, CanvasNodeType, Quote, StoredNode } from '../../api/nodeCanvas'
import { ProtectedImage, ProtectedVideo } from '../common/ProtectedMedia'
import { useCanvas } from './context'
import { ACTIVE, HANDLE_LABELS, statusText, INPUTS, latestJob, resolveInputs, selectedResult, type ImageData, type InputHandle, type TextData, type VideoData } from './graph'

const ICONS = { text: Type, image: ImageIcon, video: Film }

function money(value: number | null | undefined) {
  return value === null || value === undefined ? '未配置价格' : `¥${value.toFixed(value < 1 ? 3 : 2)}`
}

function Shell({ id, type, data, selected, children }: { id: string; type: CanvasNodeType; data: { label?: string }; selected: boolean; children: ReactNode }) {
  const { updateData, runs } = useCanvas()
  const Icon = ICONS[type]
  const job = latestJob(runs, id)
  const inputs = Object.keys(INPUTS[type]) as InputHandle[]
  return <div className={`nc-node nc-node--${type}${selected ? ' nc-node--selected' : ''}`}>
    <header className="nc-node__head">
      <Icon size={14} />
      <input className="nc-node__label nodrag" aria-label="节点名称" value={data.label || ''} placeholder={type === 'text' ? '文本' : type === 'image' ? '图片' : '视频'}
        onChange={(event) => updateData(id, { label: event.target.value })} />
      {job && <span className={`nc-status nc-status--${job.status}`} title={job.error || undefined}>
        {ACTIVE.has(job.status) && <Loader2 size={11} className="nc-spin" />}{statusText(job)}
      </span>}
    </header>
    {job?.status === 'running' && job.progress > 0 && <div className="nc-progress" role="progressbar" aria-label="生成进度"
      aria-valuenow={job.progress} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${job.progress}%` }} /></div>}
    {inputs.map((handle, index) => <Handle key={handle} id={handle} type="target" position={Position.Left}
      className={`nc-handle nc-handle--${handle}`} style={{ top: 58 + index * 34 }}>
      <span className="nc-handle__label">{HANDLE_LABELS[handle]}</span>
    </Handle>)}
    {type !== 'video' && <Handle id="out" type="source" position={Position.Right} className="nc-handle nc-handle--out" style={{ top: 58 }} />}
    {children}
  </div>
}

export const TextNode = memo(function TextNode({ id, data, selected }: NodeProps) {
  const { updateData } = useCanvas()
  const value = data as TextData
  return <Shell id={id} type="text" data={value} selected={!!selected}>
    <textarea className="nc-textarea nodrag nowheel" aria-label="文本内容" rows={6} value={value.text || ''} placeholder="写一段描述、台词或风格要求，连到图片 / 视频节点的「提示词」"
      onChange={(event) => updateData(id, { text: event.target.value })} />
  </Shell>
})

/** 结果的生成时间，如 18:49；没有时间时留空。 */
function clock(value?: string | null) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function useQuote(enabled: boolean, request: unknown) {
  const { quote } = useCanvas()
  const [result, setResult] = useState<Quote | null>(null)
  const key = JSON.stringify(request)
  useEffect(() => {
    if (!enabled) { setResult(null); return }
    let cancelled = false
    const timer = window.setTimeout(() => {
      quote(JSON.parse(key)).then((value) => { if (!cancelled) setResult(value) }).catch(() => { if (!cancelled) setResult(null) })
    }, 600)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [enabled, key, quote])
  return result
}

function ModelSelect({ kind, value, fallback, onChange }: { kind: 'image' | 'video'; value: string; fallback: string; onChange: (slug: string) => void }) {
  const { models } = useCanvas()
  const options: CanvasModel[] = models?.[kind] || []
  const fallbackName = options.find((item) => item.slug === fallback)?.name || fallback || '未设置'
  return <select className="nc-select nodrag" aria-label="模型" value={value} onChange={(event) => onChange(event.target.value)}>
    <option value="">默认 · {fallbackName}</option>
    {options.map((item) => <option key={item.slug} value={item.slug}>{item.name}{item.provider ? ` · ${item.provider}` : ''}</option>)}
  </select>
}

function Generator({ id, node, kind, children }: { id: string; node: StoredNode; kind: 'image' | 'video'; children: ReactNode }) {
  const { nodes, edges, runs, aspectRatio, run, upload } = useCanvas()
  const { request, blockers, upstreamPrompt } = useMemo(() => resolveInputs(node, nodes, edges, runs, aspectRatio), [node, nodes, edges, runs, aspectRatio])
  const quote = useQuote(blockers.length === 0, request)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const job = latestJob(runs, id)
  const running = !!job && ACTIVE.has(job.status)
  const start = async () => { setBusy(true); try { await run(id, request) } finally { setBusy(false) } }
  return <>
    {upstreamPrompt && <div className="nc-upstream" title={upstreamPrompt}>上游提示词：{upstreamPrompt}</div>}
    {children}
    {blockers.length > 0 && <div className="nc-blockers">{blockers.map((text) => <div key={text}>{text}</div>)}</div>}
    {job?.status === 'failed' && job.error && <div className="nc-error" title={job.error}>{job.error}</div>}
    <footer className="nc-node__foot">
      <input ref={fileRef} type="file" hidden accept={kind === 'image' ? 'image/png,image/jpeg,image/webp' : 'video/mp4,video/quicktime,video/webm'}
        onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void upload(id, file) }} />
      <button className="nc-icon nodrag" aria-label="上传" title={kind === 'image' ? '上传图片' : '上传视频'} onClick={() => fileRef.current?.click()}><Upload size={14} /></button>
      <span className="nc-quote">{quote ? `预计 ${money(quote.estimated_cost)}` : ''}</span>
      <button className="nc-run nodrag" disabled={busy || running || blockers.length > 0} onClick={() => void start()}>
        {busy || running ? <Loader2 size={13} className="nc-spin" /> : <Play size={13} />}生成
      </button>
    </footer>
  </>
}

function History({ id, selected, onSelect, kind }: { id: string; selected?: string; onSelect: (artifactId: string) => void; kind: 'image' | 'video' }) {
  const { runs } = useCanvas()
  const results = runs?.nodes[id]?.results || []
  if (results.length < 2) return null
  const current = selected && results.some((item) => item.artifact_id === selected) ? selected : results[0].artifact_id
  return <div className="nc-history nodrag nowheel" aria-label="生成历史">
    {results.map((result, index) => <button key={result.artifact_id} className={result.artifact_id === current ? 'nc-history__item nc-history__item--on' : 'nc-history__item'}
      aria-label={`结果 ${results.length - index}`} onClick={() => onSelect(result.artifact_id)}>
      {kind === 'image' ? <ProtectedImage src={result.url} alt="" /> : <span>
        <b>#{results.length - index}</b>{result.uploaded ? '上传' : result.tier === 'final' ? '成片' : clock(result.created_at)}
      </span>}
    </button>)}
  </div>
}

export const ImageNode = memo(function ImageNode({ id, data, selected }: NodeProps) {
  const { updateData, runs, models } = useCanvas()
  const value = data as ImageData
  const node: StoredNode = { id, type: 'image', position: { x: 0, y: 0 }, data: value as Record<string, unknown> }
  const result = selectedResult(node, runs)
  return <Shell id={id} type="image" data={value} selected={!!selected}>
    <div className="nc-preview nc-preview--image">{result ? <ProtectedImage src={result.url} alt={value.label || '图片结果'} /> : <span>还没有图片</span>}</div>
    <History id={id} kind="image" selected={value.selected} onSelect={(artifactId) => updateData(id, { selected: artifactId })} />
    <Generator id={id} node={node} kind="image">
      <textarea className="nc-textarea nodrag nowheel" aria-label="提示词" rows={3} value={value.prompt || ''} placeholder="画面描述；连入参考图时会按参考图编辑"
        onChange={(event) => updateData(id, { prompt: event.target.value })} />
      <div className="nc-row">
        <ModelSelect kind="image" value={value.model || ''} fallback={models?.defaults.image || ''} onChange={(model) => updateData(id, { model })} />
        <select className="nc-select nc-select--small nodrag" aria-label="张数" value={value.count || 1} onChange={(event) => updateData(id, { count: Number(event.target.value) })}>
          {[1, 2, 3, 4].map((count) => <option key={count} value={count}>{count} 张</option>)}
        </select>
      </div>
    </Generator>
  </Shell>
})

export const VideoNode = memo(function VideoNode({ id, data, selected }: NodeProps) {
  const { updateData, runs, models } = useCanvas()
  const value = data as VideoData
  const node: StoredNode = { id, type: 'video', position: { x: 0, y: 0 }, data: value as Record<string, unknown> }
  const result = selectedResult(node, runs)
  const tier = value.tier || 'preview'
  const fallback = tier === 'final' ? models?.defaults.video_final : models?.defaults.video_preview
  return <Shell id={id} type="video" data={value} selected={!!selected}>
    <div className="nc-preview nc-preview--video nodrag nowheel">{result ? <ProtectedVideo key={result.url} src={result.url} controls playsInline /> : <span>还没有视频</span>}</div>
    <History id={id} kind="video" selected={value.selected} onSelect={(artifactId) => updateData(id, { selected: artifactId })} />
    <Generator id={id} node={node} kind="video">
      <textarea className="nc-textarea nodrag nowheel" aria-label="提示词" rows={3} value={value.prompt || ''} placeholder="动作、运镜与氛围"
        onChange={(event) => updateData(id, { prompt: event.target.value })} />
      <div className="nc-row">
        <ModelSelect kind="video" value={value.model || ''} fallback={fallback || ''} onChange={(model) => updateData(id, { model })} />
      </div>
      <div className="nc-row">
        <div className="nc-segment nodrag" role="radiogroup" aria-label="档位">
          {(['preview', 'final'] as const).map((item) => <button key={item} role="radio" aria-checked={tier === item} className={tier === item ? 'on' : ''}
            onClick={() => updateData(id, { tier: item })}>{item === 'preview' ? '预览' : '成片'}</button>)}
        </div>
        <select className="nc-select nc-select--small nodrag" aria-label="时长" value={value.duration || 5} onChange={(event) => updateData(id, { duration: Number(event.target.value) })}>
          {[3, 4, 5, 6, 8, 10, 12].map((seconds) => <option key={seconds} value={seconds}>{seconds} 秒</option>)}
        </select>
      </div>
    </Generator>
  </Shell>
})

export const nodeTypes = { text: TextNode, image: ImageNode, video: VideoNode }
