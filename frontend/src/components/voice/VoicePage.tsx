import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Play, Square, Trash2 } from 'lucide-react'
import { useNavigate, useOutletContext, useParams } from 'react-router-dom'
import type { PresetVoice } from '../../api/nodeCanvas'
import { paths } from '../../routePaths'
import { ProtectedAudio, ProtectedImage } from '../common/ProtectedMedia'
import { BatchDialog } from '../nodeCanvas/BatchDialog'
import { resolveInputs } from '../nodeCanvas/graph'
import type { ProjectOutletContext } from '../project/outletContext'
import { useNodeQuote } from '../storyboard/ShotFields'
import { buildShots, priceSuffix, type Shot } from '../storyboard/shots'
import { toast } from '../ui/Toast'
import { assignLine, buildLines, buildSpeakers, extendedDuration, linesOfShot, pendingCount, stateLabel, voicePatch, type Speaker, type VoiceLine } from './voice'
import { useVoice, type VoiceData } from './useVoice'
import './voice.css'

const MAX_LINE_CHARS = 500

/** 试听：有 sample_url 才显示按钮（没有示例音频不去调用付费 TTS）。 */
function Preview({ voice }: { voice?: PresetVoice }) {
  const [playing, setPlaying] = useState(false)
  const audio = useRef<HTMLAudioElement | null>(null)
  useEffect(() => () => { audio.current?.pause() }, [])
  if (!voice?.sample_url) return null
  const toggle = () => {
    if (playing) { audio.current?.pause(); setPlaying(false); return }
    const player = new Audio(voice.sample_url as string)
    audio.current = player
    player.onended = () => setPlaying(false)
    setPlaying(true)
    void player.play().catch(() => { setPlaying(false); toast('试听播放失败', 'error') })
  }
  return (
    <button type="button" className="vc-btn vc-btn--sm" onClick={toggle}>
      {playing ? <Square size={11} aria-hidden="true" /> : <Play size={11} aria-hidden="true" />}{playing ? '停止' : '试听'}
    </button>
  )
}

function SpeakerCard({ speaker, voices, onPick }: { speaker: Speaker; voices: PresetVoice[]; onPick: (voice: string) => void }) {
  const chosen = voices.find((item) => item.id === speaker.voice)
  const missing = !speaker.voice
  return (
    <div className={`vc-speaker${missing ? ' vc-speaker--missing' : ''}`}>
      <div className="vc-speaker__head">
        <span className="vc-avatar" aria-hidden="true">{speaker.name.slice(0, 1)}</span>
        <span className="vc-speaker__name">{speaker.name}</span>
        <span className="vc-muted">{speaker.count} 句</span>
        {missing && <span className="vc-speaker__tag">未选择</span>}
      </div>
      <label className="vc-field">音色
        <select aria-label={`${speaker.name}的音色`} value={speaker.voice} onChange={(event) => onPick(event.target.value)}>
          <option value="">选择一个音色</option>
          {speaker.voice && !chosen && <option value={speaker.voice}>{speaker.voice}</option>}
          {voices.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.style}</option>)}
        </select>
      </label>
      <Preview voice={chosen} />
    </div>
  )
}

function LinePlayer({ url }: { url: string }) {
  const [open, setOpen] = useState(false)
  if (open) return <ProtectedAudio src={url} autoPlay controls className="vc-audio" onEnded={() => setOpen(false)} />
  return <button type="button" className="vc-play" aria-label="播放" onClick={() => setOpen(true)}><Play size={13} aria-hidden="true" /></button>
}

/** 镜头视频节点允许的时长范围：按它所选的模型（没选就是档位对应的默认模型）；查不到就不收窄。 */
function videoLimits(video: Shot['video'], models: VoiceData['models']): { min?: number; max?: number } {
  if (!video || !models) return {}
  const data = video.data as { model?: string; tier?: string }
  const slug = data.model || (data.tier === 'final' ? models.defaults.video_final : models.defaults.video_preview)
  const model = models.video.find((item) => item.slug === slug)
  return { min: model?.min_duration, max: model?.max_duration }
}

function LineRow({ line, data, shots, editing, onEdit, extended, onExtended }: {
  line: VoiceLine; data: VoiceData; shots: Shot[]; editing: boolean; onEdit: (on: boolean) => void
  /** 这句台词的镜头刚被延长到几秒（提示需要重新生成视频） */
  extended?: number; onExtended: (seconds: number) => void
}) {
  const resolved = resolveInputs(line.node, data.nodes, data.edges, data.runs, data.aspectRatio)
  const generable = line.state === 'todo' || line.state === 'failed' || (editing && line.state === 'done')
  const { price, error } = useNodeQuote(data.quote, resolved.request, generable && resolved.blockers.length === 0)
  const [busy, setBusy] = useState(false)
  const field = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { if (editing) field.current?.focus() }, [editing])
  const start = async () => { setBusy(true); try { await data.run(line.node.id, resolved.request) } finally { setBusy(false) } }
  const job = data.runs?.nodes[line.node.id]?.jobs[0]
  const reasons = error ? [...resolved.blockers, error] : resolved.blockers
  // 台词来自连入的文本节点时，编辑改的就是那个文本节点（只连了一个时）；连了多个就只读
  const linked = line.linkedTextIds.length > 0
  const editable = !linked || line.linkedTextIds.length === 1
  const editId = linked ? line.linkedTextIds[0] : line.node.id
  const textEditing = (editing || line.state === 'empty') && editable
  const shotLabel = line.shot ? `镜头 ${line.shot.index + 1}` : '未归属镜头'
  const extend = () => {
    const video = line.shot?.video
    if (!video || line.ms === null) return
    const target = extendedDuration(line.ms, videoLimits(video, data.models))
    if (target === null) { toast('这个视频模型最长的镜头也放不下这段配音，请缩短台词', 'error'); return }
    data.updateData(video.id, { duration: target })
    toast(`镜头已延长到 ${target} 秒，需要重新生成这个镜头的视频`, 'success')
    onExtended(target)
  }
  const assign = (shotId: string) => {
    const shot = shots.find((item) => item.id === shotId)
    if (!shot) return
    const { edge, order } = assignLine(line.node, shot, linesOfShot(shot, data.nodes, data.edges))
    data.update((current) => ({
      nodes: current.nodes.map((node) => node.id === line.node.id ? { ...node, data: { ...node.data, order } } : node),
      edges: [...current.edges, edge],
    }))
  }
  const remove = () => data.update((current) => ({
    nodes: current.nodes.filter((node) => node.id !== line.node.id),
    edges: current.edges.filter((edge) => edge.source !== line.node.id && edge.target !== line.node.id),
  }))
  return (
    <li className={`vc-line${line.overBy !== null ? ' vc-line--warn' : ''}`}>
      <div className="vc-line__thumb" aria-hidden="true">
        {line.shot?.thumbnail ? <ProtectedImage src={line.shot.thumbnail.url} alt="" /> : null}
      </div>
      <div className="vc-line__body">
        <span className="vc-muted">{shotLabel} · {line.speaker}{linked ? ' · 来自文本节点' : ''}</span>
        {textEditing
          ? <textarea ref={field} className="vc-line__edit" aria-label={`${shotLabel} 台词`} rows={2} maxLength={MAX_LINE_CHARS}
            value={String((data.nodes.find((node) => node.id === editId)?.data as { text?: string } | undefined)?.text || '')}
            placeholder="写这一句台词" onChange={(event) => data.updateData(editId, { text: event.target.value })} />
          : <span className="vc-line__text">{line.text || '（台词为空）'}</span>}
        {linked && !editable && <span className="vc-muted">连了多个文本节点，请到画布里修改</span>}
        {line.overBy !== null && line.seconds !== null && <span className="vc-line__warn">配音 {line.seconds} 秒，比镜头长 {line.overBy} 秒</span>}
        {extended !== undefined && <span className="vc-muted">镜头已延长到 {extended} 秒，需要重新生成视频</span>}
        {line.state === 'failed' && <span className="vc-line__error">{line.error || '生成失败'}</span>}
        {reasons.length > 0 && line.state !== 'done' && line.state !== 'running' && line.state !== 'needs_voice' && line.state !== 'empty' && <span className="vc-line__warn">{reasons[0]}</span>}
      </div>
      <div className="vc-line__side">
        {line.state === 'done' && line.seconds !== null && <span className="vc-muted">{line.seconds} 秒</span>}
        {line.state === 'done' && line.overBy === null && !editing && <span className="vc-done">{stateLabel.done}</span>}
        {line.state === 'done' && line.result && <LinePlayer url={line.result.url} />}
        {line.state === 'running' && <span className="vc-running"><Loader2 size={12} className="vc-spin" aria-hidden="true" />{stateLabel.running}{job && job.status === 'running' && job.progress > 0 ? ` ${job.progress}%` : ''}</span>}
        {line.state === 'needs_voice' && <span className="vc-muted">{stateLabel.needs_voice}</span>}
        {line.state === 'empty' && <span className="vc-muted">{stateLabel.empty}</span>}
        {generable && (
          <button type="button" className="vc-btn vc-btn--sm" disabled={busy || reasons.length > 0} title={reasons[0]} onClick={() => void start()}>
            {busy && <Loader2 size={11} className="vc-spin" aria-hidden="true" />}
            {line.state === 'failed' ? '重试' : line.state === 'done' ? '重新生成' : '生成'}{priceSuffix(price)}
          </button>
        )}
        {line.overBy !== null && !editing && <>
          <button type="button" className="vc-btn vc-btn--sm" onClick={extend}>延长镜头</button>
          <button type="button" className="vc-btn vc-btn--sm" disabled={!editable} onClick={() => onEdit(true)}>缩短台词</button>
        </>}
        {editing && line.state !== 'empty' && <button type="button" className="vc-btn vc-btn--sm" onClick={() => onEdit(false)}>完成</button>}
        {!line.shot && <>
          <select className="vc-assign" aria-label={`把「${line.text || line.speaker}」归到镜头`} value="" disabled={shots.length === 0} onChange={(event) => assign(event.target.value)}>
            <option value="">归到镜头…</option>
            {shots.map((shot) => <option key={shot.id} value={shot.id}>镜头 {shot.index + 1}</option>)}
          </select>
          <button type="button" className="vc-icon" aria-label={`删除「${line.text || line.speaker}」`} disabled={line.state === 'running'} onClick={remove}><Trash2 size={13} aria-hidden="true" /></button>
        </>}
      </div>
    </li>
  )
}

/** 第 4 步：配音页。左侧选角色音色，右侧按镜头排台词并生成配音（读写画布 graph 里的配音节点与角色节点，不新建任何表）。 */
export default function VoicePage() {
  const { canvasId = '' } = useParams()
  const navigate = useNavigate()
  const outlet = useOutletContext<ProjectOutletContext | null>()
  const refreshSummary = outlet?.refreshSummary
  const setFooter = outlet?.setFooter
  const data = useVoice(canvasId)
  const shots = useMemo(() => buildShots(data.nodes, data.edges, data.runs), [data.nodes, data.edges, data.runs])
  const lines = useMemo(() => buildLines(shots, data.nodes, data.edges, data.runs), [shots, data.nodes, data.edges, data.runs])
  const speakers = useMemo(() => buildSpeakers(lines, data.nodes, data.edges), [lines, data.nodes, data.edges])
  const [editingId, setEditingId] = useState<string | null>(null)
  const [extended, setExtended] = useState<Record<string, number>>({})

  // 保存完成或结果变化后，让项目页的步骤条 / 底栏尽快更新，不等轮询
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; return }
    refreshSummary?.()
  }, [data.revision, data.signature, refreshSummary])

  const pending = pendingCount(lines)
  const silent = speakers.filter((item) => !item.voice).map((item) => item.name)
  const goNext = useRef<() => void>(() => undefined)
  goNext.current = () => {
    void data.flushThen().then(() => navigate(paths.projectStep(canvasId, 'final'))).catch((reason) => {
      toast(reason instanceof Error ? reason.message : '保存失败，请重试', 'error')
    })
  }
  const footerNote = pending > 0 ? `还差 ${pending} 句台词没配音` : ''
  const footerHint = silent.length > 0 ? `${silent.slice(0, 3).join('、')}还没有音色` : ''
  useEffect(() => {
    if (!data.loaded) return
    setFooter?.({ label: '配音完成，去成片 →', onClick: () => goNext.current(), disabled: pending > 0, note: footerNote || undefined, hint: footerHint || undefined })
  }, [setFooter, data.loaded, pending, footerNote, footerHint])
  useEffect(() => () => setFooter?.(null), [setFooter])

  const pick = (speaker: Speaker, voice: string) => {
    const patch = voicePatch(speaker, lines, voice)
    data.updateMany(Object.fromEntries(Object.entries(patch).map(([id, value]) => [id, { voice: value }])))
  }

  if (data.error) return <div className="vc-message" role="alert">{data.error}</div>
  if (!data.loaded) return <div className="vc-message" aria-busy="true">配音加载中…</div>

  // 顶部“生成剩余”：只跑还没有结果的配音节点；有生成中 / 待选音色时先处理，免得重复提交；空台词节点后端会自动跳过
  const remaining = lines.filter((line) => line.state === 'todo' || line.state === 'failed').length
  const running = lines.some((line) => line.state === 'running')
  const needVoice = lines.find((line) => line.state === 'needs_voice')
  const blocked = data.submitting ? '正在提交' : running ? '有台词正在生成' : needVoice ? `先给${needVoice.speaker}选一个音色` : ''
  const total = data.estimate.quote?.estimated_total
  const batchRunning = data.batch?.status === 'running' && data.batch.kinds?.length === 1 && data.batch.kinds[0] === 'tts'
  const counted = lines.filter((line) => line.state !== 'empty')
  const placedLines = lines.filter((line) => line.shot)
  const looseLines = lines.filter((line) => !line.shot)
  const renderRow = (line: VoiceLine) => (
    <LineRow key={line.node.id} line={line} data={data} shots={shots} editing={editingId === line.node.id}
      onEdit={(on) => setEditingId(on ? line.node.id : null)}
      extended={extended[line.node.id]} onExtended={(seconds) => setExtended((value) => ({ ...value, [line.node.id]: seconds }))} />
  )

  return (
    <div className="vc">
      <aside className="vc-side" aria-label="角色音色">
        <h2>角色音色</h2>
        {speakers.length === 0
          ? <p className="vc-muted">还没有台词。到分镜页给镜头写台词后，这里会列出说话人。</p>
          : speakers.map((speaker) => <SpeakerCard key={speaker.name} speaker={speaker} voices={data.voices} onPick={(voice) => pick(speaker, voice)} />)}
      </aside>
      <section className="vc-lines" aria-label="台词列表">
        <div className="vc-lines__head">
          <h2>按镜头的台词 · {counted.length} 句</h2>
          {batchRunning && data.batch && <span className="vc-muted" role="status">配音批次 {data.batch.completed}/{data.batch.total}</span>}
          {counted.length > 0 && (
            <button type="button" className="vc-btn" disabled={remaining === 0 || !!blocked} title={blocked || undefined} onClick={() => void data.openBatch()}>
              生成剩余 {remaining} 句{total !== undefined && remaining > 0 ? ` · 预估 ¥${total.toFixed(total < 1 ? 3 : 2)}` : ''}
            </button>
          )}
        </div>
        {blocked && counted.length > 0 && <p className="vc-hint" role="note">{blocked}，处理后才能批量生成。</p>}
        {lines.length === 0
          ? <div className="vc-empty"><h3>还没有台词</h3><p>没有台词也可以直接去成片；要配音的话，到分镜页的「台词」里给镜头添加。</p></div>
          : <>
            {placedLines.length > 0 && <ul className="vc-list">{placedLines.map(renderRow)}</ul>}
            {looseLines.length > 0 && <section className="vc-loose" aria-label="未归属镜头的台词">
              <h3>未归属镜头的台词 · {looseLines.filter((line) => line.state !== 'empty').length} 句</h3>
              <p className="vc-muted">这些配音节点没有连到任何镜头：可以生成、删除，或归到某个镜头。</p>
              <ul className="vc-list">{looseLines.map(renderRow)}</ul>
            </section>}
          </>}
      </section>
      {data.plan && (
        <div className="vc-drawer">
          <BatchDialog plan={{ ...data.plan, busy: data.plan.busy || data.submitting, notice: data.submitting ? '正在提交，请稍候…' : data.plan.notice }} onConfirm={() => void data.confirmBatch()} onClose={() => data.setPlan(null)} />
        </div>
      )}
    </div>
  )
}
