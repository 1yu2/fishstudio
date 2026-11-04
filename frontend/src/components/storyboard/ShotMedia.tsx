import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import type { NodeResult, Quote } from '../../api/nodeCanvas'
import { ProtectedImage, ProtectedVideo } from '../common/ProtectedMedia'
import { newRequestId, selectedResult } from '../nodeCanvas/graph'
import { EDIT_QUICK_PHRASES, appendPhrase, buildEditChain, editRequest, priceSuffix, type Shot } from './shots'
import type { Storyboard } from './useStoryboard'

type Tab = 'image' | 'video'

/** 修改记录：画面节点的所有结果按 edit_of 串成链，点任意一版即选用它，并作为下次修改的基础。 */
function EditChain({ shot, sb }: { shot: Shot; sb: Storyboard }) {
  const frame = shot.frame
  const results = frame ? sb.runs?.nodes[frame.id]?.results || [] : []
  const chain = buildEditChain(results.filter((item) => item.type !== 'video'))
  if (!frame || chain.length === 0) return null
  const current = selectedResult(frame, sb.runs)?.artifact_id
  const hasEdits = chain.some((item) => item.edited)
  return (
    <div className="sb-chain">
      <span className="sb-chain__title">{hasEdits ? '修改记录' : '生成结果'}</span>
      <div className="sb-chain__list">
        {chain.map((item, index) => {
          const on = item.result.artifact_id === current
          const previous = chain[index - 1]
          return (
            <div key={item.result.artifact_id} className="sb-chain__entry">
              {previous && item.edited && <span className="sb-chain__arrow" aria-hidden="true">→</span>}
              <button
                type="button"
                className={`sb-chain__item${on ? ' sb-chain__item--on' : ''}`}
                aria-pressed={on}
                aria-label={`${item.label}${on ? ' 选用' : ''}`}
                title={item.basedOn ? `基于「${item.basedOn}」修改` : undefined}
                onClick={() => sb.updateData(frame.id, { selected: item.result.artifact_id })}
              >
                <span className="sb-chain__thumb"><ProtectedImage src={item.result.url} alt="" /></span>
                <span className="sb-chain__label">{item.label}{on ? ' · 选用' : ''}</span>
              </button>
            </div>
          )
        })}
      </div>
      <span className="sb-chain__hint">点任意一张可回到那一版继续改</span>
    </div>
  )
}

/** 基于当前选用的图继续修改：写要求 → 出 1–4 张 → 提交 edit_of 请求。 */
function EditPanel({ shot, sb }: { shot: Shot; sb: Storyboard }) {
  const frame = shot.frame
  const [text, setText] = useState('')
  const [keep, setKeep] = useState(true)
  const [count, setCount] = useState(2)
  const [busy, setBusy] = useState(false)
  const [estimate, setEstimate] = useState<Quote | null>(null)
  const [quoteError, setQuoteError] = useState('')
  const base = frame ? selectedResult(frame, sb.runs) : undefined
  const results = frame ? sb.runs?.nodes[frame.id]?.results || [] : []
  const baseLabel = base ? buildEditChain(results).find((item) => item.result.artifact_id === base.artifact_id)?.label : undefined
  const model = String((frame?.data as { model?: string } | undefined)?.model || '')
  const aspectRatio = sb.aspectRatio
  // 基于某张图修改走图片编辑路由，用编辑专用的原因（普通生成的 image_problem 查的是另一条默认路由）
  const problem = !model && sb.models?.image_edit_problem ? sb.models.image_edit_problem : ''
  const baseId = base?.artifact_id
  const quoteFn = sb.quote

  // 预估价只取决于基础图 / 张数 / 模型，与要求文字无关
  useEffect(() => {
    if (!baseId || problem) { setEstimate(null); setQuoteError(''); return }
    let cancelled = false
    const timer = window.setTimeout(() => {
      quoteFn(editRequest({ base: baseId, text: '修改', keep: false, count, model, aspectRatio }))
        .then((value) => { if (!cancelled) { setEstimate(value); setQuoteError('') } })
        .catch((reason) => { if (!cancelled) { setEstimate(null); setQuoteError(reason instanceof Error && reason.message ? reason.message : '暂时无法报价') } })
    }, 400)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [baseId, count, model, aspectRatio, problem, quoteFn])

  if (!frame) return null
  if (!base) return <div className="sb-edit sb-edit--empty">先生成一张画面，之后就可以基于它继续修改。</div>
  const submit = async () => {
    if (!text.trim() || busy) return
    setBusy(true)
    const request = { ...editRequest({ base: base.artifact_id, text, keep, count, model, aspectRatio }), client_request_id: newRequestId() }
    const ok = await sb.run(frame.id, request)
    setBusy(false)
    if (ok) setText('')
  }
  return (
    <div className="sb-edit">
      <span className="sb-edit__title">基于“{baseLabel || '当前这张'}”继续修改</span>
      <label className="sb-field sb-field--small">想改哪里
        <textarea rows={2} value={text} maxLength={500} onChange={(event) => setText(event.target.value)} placeholder="例如：表情再冷一点，嘴角不要笑" />
      </label>
      <div className="sb-edit__chips">
        {EDIT_QUICK_PHRASES.map((phrase) => (
          <button key={phrase} type="button" className="sb-chip" onClick={() => setText((value) => appendPhrase(value, phrase))}>{phrase}</button>
        ))}
      </div>
      <label className="sb-check"><input type="checkbox" checked={keep} onChange={(event) => setKeep(event.target.checked)} /> 保持构图和角色长相</label>
      {(problem || quoteError) && <div className="sb-problem" role="alert">{problem || quoteError}</div>}
      <div className="sb-edit__foot">
        <label className="sb-inline">出
          <select aria-label="修改张数" value={count} onChange={(event) => setCount(Number(event.target.value))}>
            {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n} 张</option>)}
          </select>
        </label>
        <button type="button" className="sb-btn sb-btn--primary" disabled={!text.trim() || busy || !!problem || !!quoteError} onClick={() => void submit()}>
          {busy && <Loader2 size={12} className="sb-spin" aria-hidden="true" />}
          按要求修改{priceSuffix(estimate)}
        </button>
      </div>
    </div>
  )
}

function VideoPreview({ shot, sb }: { shot: Shot; sb: Storyboard }) {
  const video = shot.video
  if (!video) return <div className="sb-preview sb-preview--empty">这个镜头还没有视频节点</div>
  const results: NodeResult[] = sb.runs?.nodes[video.id]?.results || []
  const current = selectedResult(video, sb.runs)
  return (
    <>
      <div className="sb-preview">
        {current ? <ProtectedVideo key={current.url} src={current.url} controls playsInline /> : <span>还没有视频</span>}
      </div>
      {results.length > 1 && (
        <div className="sb-chain__list" aria-label="视频结果">
          {results.map((item, index) => (
            <button key={item.artifact_id} type="button" className={`sb-chip${item.artifact_id === current?.artifact_id ? ' sb-chip--on' : ''}`}
              aria-pressed={item.artifact_id === current?.artifact_id} onClick={() => sb.updateData(video.id, { selected: item.artifact_id })}>
              视频 {results.length - index}
            </button>
          ))}
        </div>
      )}
    </>
  )
}

/** 镜头详情左侧：大图 / 视频切换、修改记录、基于这张修改。 */
/** "9:16" → "9 / 16"；格式不对时按竖屏处理。 */
function cssRatio(value: string): string {
  const match = /^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$/.exec(value)
  return match ? `${match[1]} / ${match[2]}` : '9 / 16'
}

export default function ShotMedia({ shot, sb }: { shot: Shot; sb: Storyboard }) {
  const [tab, setTab] = useState<Tab>('image')
  const status = shot.frameStatus
  return (
    <div className="sb-media">
      <div className="sb-seg" role="group" aria-label="预览类型">
        {(['image', 'video'] as const).map((item) => (
          <button key={item} type="button" className={`sb-seg__item${tab === item ? ' sb-seg__item--on' : ''}`} aria-pressed={tab === item} onClick={() => setTab(item)}>
            {item === 'image' ? '图片' : '视频'}
          </button>
        ))}
      </div>
      {tab === 'image' ? (
        <>
          <div className="sb-preview" style={{ aspectRatio: cssRatio(sb.aspectRatio) }}>
            {shot.thumbnail ? <ProtectedImage src={shot.thumbnail.url} alt={`镜头 ${shot.index + 1} 画面`} /> : (
              <span>{status.state === 'running' ? (status.progress > 0 ? `生成中 ${status.progress}%` : status.queued ? '排队中' : '生成中') : '还没有画面'}</span>
            )}
          </div>
          <EditChain shot={shot} sb={sb} />
          <EditPanel key={shot.id} shot={shot} sb={sb} />
        </>
      ) : <VideoPreview shot={shot} sb={sb} />}
    </div>
  )
}
