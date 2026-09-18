import { useCallback, useEffect, useState } from 'react'
import * as seriesApi from '../../api/series'
import type { SeriesEpisode, TimelineRevision } from '../../api/series'
import { toast } from '../ui/Toast'

export default function TimelinePanel({ episodes, onChanged }: { episodes: SeriesEpisode[]; onChanged: () => void }) {
  const [episodeId, setEpisodeId] = useState(episodes[0]?.id || '')
  const [revisions, setRevisions] = useState<TimelineRevision[]>([])
  const [selected, setSelected] = useState<TimelineRevision | null>(null)
  const [editor, setEditor] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    if (!episodeId) return
    const rows = await seriesApi.listTimelineRevisions(episodeId)
    setRevisions(rows)
    const row = rows[0] || null
    setSelected(row)
    setEditor(row ? JSON.stringify(row.spec, null, 2) : '')
  }, [episodeId])

  useEffect(() => { void load() }, [load])

  const act = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true)
    try {
      await fn()
      toast(message, 'success')
      await load()
      onChanged()
    } catch (error) {
      toast(error instanceof Error ? error.message : '操作失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  const saveRevision = async () => {
    let spec: Record<string, unknown>
    try { spec = JSON.parse(editor) as Record<string, unknown> } catch { return toast('Timeline JSON 格式错误', 'error') }
    await act(
      () => seriesApi.createTimelineRevision(episodeId, { spec, parent_revision_id: selected?.id }),
      '已创建新的 Timeline revision',
    )
  }

  return (
    <section className="studio__card timeline-panel">
      <div className="studio__section-title" style={{ marginTop: 0 }}>Timeline / EDL</div>
      <div className="studio__toolbar">
        <select className="studio__input" value={episodeId} onChange={(event) => setEpisodeId(event.target.value)}>
          {episodes.map((episode) => <option key={episode.id} value={episode.id}>第 {episode.index} 集 · {episode.title || '未命名'}</option>)}
        </select>
        <button className="studio__btn" disabled={busy || !episodeId} onClick={() => act(() => seriesApi.planTimelineRevision(episodeId), '已从正式候选生成 EDL')}>生成 Timeline</button>
        <button className="studio__btn" disabled={busy || !selected} onClick={saveRevision}>保存为新版本</button>
        <button className="studio__btn studio__btn--primary" disabled={busy || !selected || selected.status === 'approved'} onClick={() => selected && act(() => seriesApi.approveTimelineRevision(episodeId, selected.id), 'Timeline 已审批')}>审批当前版本</button>
      </div>
      <div className="studio__muted">
        {selected ? `Revision ${selected.revision} · ${selected.status} · ${selected.content_hash.slice(0, 12)}` : '暂无 Timeline，请先从已选视频候选生成。'}
      </div>
      <div className="timeline-revisions">
        {revisions.map((revision) => (
          <button key={revision.id} className={`studio__btn studio__btn--sm${selected?.id === revision.id ? ' studio__btn--primary' : ''}`} onClick={() => { setSelected(revision); setEditor(JSON.stringify(revision.spec, null, 2)) }}>
            r{revision.revision} · {revision.status}
          </button>
        ))}
      </div>
      <textarea className="studio__textarea timeline-editor" value={editor} onChange={(event) => setEditor(event.target.value)} spellCheck={false} placeholder="Timeline spec JSON" />
    </section>
  )
}
