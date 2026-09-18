import { useEffect, useMemo, useState } from 'react'
import * as seriesApi from '../../api/series'
import type { ReferenceAnalysis, ReferenceSegment, StoryboardData } from '../../api/series'
import { toast } from '../ui/Toast'

export default function ReferenceAnalysisPanel({ projectId, storyboard, onChanged }: {
  projectId: string
  storyboard: StoryboardData | null
  onChanged: () => void
}) {
  const [rows, setRows] = useState<ReferenceAnalysis[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [duration, setDuration] = useState(8)
  const [authorization, setAuthorization] = useState<'owned' | 'licensed' | 'public_domain'>('owned')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<Record<string, string>>({})
  const shots = useMemo(() => (storyboard?.episodes || []).flatMap((episode) =>
    episode.scenes.flatMap((scene) => scene.shots.map((shot) => ({ id: shot.id, label: `E${episode.index} · ${scene.title || `场景${scene.index}`} · 镜头${shot.index}` })))), [storyboard])

  const load = async () => {
    try {
      const list = await seriesApi.listReferenceAnalyses(projectId)
      setRows(list)
      setEditing(Object.fromEntries(list.map((row) => [row.id, JSON.stringify(row.segments, null, 2)])))
    } catch (e) { toast(e instanceof Error ? e.message : '参考分析加载失败', 'error') }
  }
  useEffect(() => { void load() }, [projectId])

  const upload = async () => {
    if (!file || busy) return
    setBusy(true)
    try {
      const artifact = await seriesApi.uploadReferenceVideo(projectId, file)
      await seriesApi.analyzeReferenceVideo(projectId, {
        artifact_id: artifact.artifact_id, duration_ms: artifact.duration_ms || Math.round(duration * 1000), authorization,
        source_uri: file.name, usage_scope: 'director_language_only',
      })
      toast('参考视频已拆解为可编辑导演语言', 'success')
      await load(); onChanged()
    } catch (e) { toast(e instanceof Error ? e.message : '分析失败', 'error') } finally { setBusy(false) }
  }

  const save = async (row: ReferenceAnalysis) => {
    try {
      const segments = JSON.parse(editing[row.id]) as ReferenceSegment[]
      await seriesApi.reviseReferenceAnalysis(row.id, segments)
      toast('已创建人工修订版本；引用旧版本的镜头已标记 stale', 'success')
      await load(); onChanged()
    } catch (e) { toast(e instanceof Error ? e.message : '修订保存失败', 'error') }
  }

  const bind = async (row: ReferenceAnalysis, segment: ReferenceSegment, element: HTMLButtonElement) => {
    const container = element.closest('[data-segment]')
    const shotId = (container?.querySelector('[data-shot]') as HTMLSelectElement | null)?.value
    const type = (container?.querySelector('[data-type]') as HTMLSelectElement | null)?.value as 'composition' | 'motion' | 'timing'
    if (!shotId) return toast('请先选择镜头', 'error')
    try {
      await seriesApi.bindReferenceSegment(row.id, { shot_id: shotId, segment_key: segment.key, reference_type: type, replace_subject: true })
      toast(`已仅复用${type}，项目主体替换已锁定`, 'success'); onChanged()
    } catch (e) { toast(e instanceof Error ? e.message : '绑定失败', 'error') }
  }

  return <>
    <section className="studio__card">
      <div className="studio__section-title" style={{ marginTop: 0 }}>参考视频拉片</div>
      <p className="studio__muted">只提取构图、运镜和节奏；不会把参考人物或场景直接传给生成模型。</p>
      <input className="studio__input" type="file" accept="video/*" onChange={(e) => setFile(e.target.files?.[0] || null)} />
      <div style={{ display: 'flex', gap: 10 }}>
        <label className="studio__muted">时长（秒）<input className="studio__input" type="number" min={0.1} step={0.1} value={duration} onChange={(e) => setDuration(Number(e.target.value))} /></label>
        <label className="studio__muted">素材授权<select className="studio__input" value={authorization} onChange={(e) => setAuthorization(e.target.value as typeof authorization)}><option value="owned">自有</option><option value="licensed">已授权</option><option value="public_domain">公版</option></select></label>
      </div>
      <button className="studio__btn studio__btn--primary" disabled={!file || busy} onClick={() => void upload()}>{busy ? '分析中…' : '上传并分析'}</button>
    </section>
    {rows.map((row) => <section className="studio__card" key={row.id}>
      <div className="studio__section-title" style={{ marginTop: 0 }}>版本 {row.revision} · {row.status}</div>
      <div className="studio__muted">置信度 {Math.round(row.confidence * 100)}% · {row.duration_ms / 1000}s · 授权 {row.authorization} · ¥{row.estimated_cost}</div>
      {row.status !== 'superseded' && row.segments.map((segment) => <div key={segment.key} data-segment className="studio__card" style={{ marginTop: 10 }}>
        <b>{segment.key}</b> <span className="studio__muted">{segment.start_ms}–{segment.end_ms}ms · {segment.camera_movement} · {segment.shot_size}</span>
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <select data-shot className="studio__input"><option value="">选择镜头</option>{shots.map((shot) => <option key={shot.id} value={shot.id}>{shot.label}</option>)}</select>
          <select data-type className="studio__input"><option value="motion">运镜</option><option value="timing">节奏</option><option value="composition">构图</option></select>
          <button className="studio__btn" onClick={(e) => void bind(row, segment, e.currentTarget)}>绑定</button>
        </div>
      </div>)}
      {row.status !== 'superseded' && <><textarea className="studio__textarea" rows={12} value={editing[row.id] || ''} onChange={(e) => setEditing({ ...editing, [row.id]: e.target.value })} /><button className="studio__btn" onClick={() => void save(row)}>保存人工修订</button></>}
    </section>)}
  </>
}
