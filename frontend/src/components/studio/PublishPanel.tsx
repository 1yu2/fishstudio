import { useCallback, useEffect, useState } from 'react'
import * as seriesApi from '../../api/series'
import type { Publication, QCReport, SeriesEpisode } from '../../api/series'
import { toast } from '../ui/Toast'

export default function PublishPanel({ projectId, episodes, onChanged }: { projectId: string; episodes: SeriesEpisode[]; onChanged: () => void }) {
  const [episodeId, setEpisodeId] = useState(episodes[0]?.id || '')
  const [reports, setReports] = useState<QCReport[]>([])
  const [publications, setPublications] = useState<Publication[]>([])
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [tags, setTags] = useState('AI短剧,小红书')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    if (!episodeId) return
    const [nextReports, nextPublications] = await Promise.all([
      seriesApi.listEpisodeQC(episodeId), seriesApi.listPublications(projectId),
    ])
    setReports(nextReports)
    setPublications(nextPublications.filter((item) => item.episode_id === episodeId))
  }, [episodeId, projectId])
  useEffect(() => { void load() }, [load])

  const act = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true)
    try { await fn(); toast(message, 'success'); await load(); onChanged() }
    catch (error) { toast(error instanceof Error ? error.message : '操作失败', 'error') }
    finally { setBusy(false) }
  }

  return <section className="studio__card publish-panel">
    <div className="studio__section-title" style={{ marginTop: 0 }}>Final QC / Publish Package</div>
    <div className="studio__toolbar">
      <select className="studio__input" value={episodeId} onChange={(event) => setEpisodeId(event.target.value)}>
        {episodes.map((episode) => <option key={episode.id} value={episode.id}>第 {episode.index} 集 · {episode.title || '未命名'}</option>)}
      </select>
      <button className="studio__btn" disabled={busy} onClick={() => act(() => seriesApi.runEpisodeQC(episodeId, { phase: 'director_review' }), '导演终审完成')}>导演终审</button>
      <button className="studio__btn studio__btn--primary" disabled={busy} onClick={() => act(() => seriesApi.runEpisodeQC(episodeId, { phase: 'final_qc' }), 'Final QC 完成')}>运行 Final QC</button>
    </div>
    <div className="qc-report-list">
      {reports.map((report) => <div className={`qc-report qc-report--${report.status}`} key={report.id}>
        <b>{report.phase} · {report.status} · {Math.round(report.score * 100)}%</b>
        {report.issues.length === 0 ? <span className="studio__muted">全部检查通过</span> : report.issues.map((issue, index) => <span key={index}>{issue.code}：{issue.detail || '未通过'} → {report.repair_targets.find((target) => target.issue_code === issue.code)?.return_to}</span>)}
      </div>)}
    </div>
    <div className="studio__grid-2">
      <input className="studio__input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="发布标题（留空自动生成）" />
      <input className="studio__input" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="标签，逗号分隔" />
    </div>
    <textarea className="studio__textarea" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="发布简介（留空自动生成）" />
    <button className="studio__btn studio__btn--primary" disabled={busy} onClick={() => act(() => seriesApi.createPublishPackage(episodeId, { title, description, tags: tags.split(',').map((item) => item.trim()).filter(Boolean) }), '人工发布包已生成')}>生成发布包</button>
    {publications.map((item) => <div className="publish-package" key={item.id}>
      <b>{item.title}</b><span className="studio__badge studio__badge--succeeded">{item.status}</span>
      <span className="studio__muted">{item.platform} · 成片 {item.render_artifact_id.slice(0, 8)} · 封面 {item.cover_artifact_id?.slice(0, 8)}</span>
      <span className="studio__muted">仅生成素材包，不会自动发布。</span>
    </div>)}
  </section>
}
