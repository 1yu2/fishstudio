import { useEffect, useState } from 'react'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { ScriptRevision, SeriesEpisode } from '../../api/series'
import './studio.css'

const EMPTY_SCENES = JSON.stringify(
  [
    {
      number: 1,
      slugline: '场景 1',
      location: '',
      time_of_day: '',
      objective: '',
      action: [],
      dialogue: [],
      narration: [],
    },
  ],
  null,
  2,
)

export default function ScriptPanel({
  projectId,
  episodes,
  onChanged,
}: {
  projectId: string
  episodes: SeriesEpisode[]
  onChanged: () => void
}) {
  const [episodeIndex, setEpisodeIndex] = useState(0)
  const [revisions, setRevisions] = useState<ScriptRevision[]>([])
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [objective, setObjective] = useState('')
  const [hook, setHook] = useState('')
  const [conflict, setConflict] = useState('')
  const [payoff, setPayoff] = useState('')
  const [cliffhanger, setCliffhanger] = useState('')
  const [scenesText, setScenesText] = useState(EMPTY_SCENES)

  const episode = episodes[episodeIndex]
  const current = revisions[0]

  const load = async () => {
    if (!episode?.id) return
    try {
      setRevisions(await seriesApi.listScriptRevisions(episode.id))
    } catch (error) {
      toast(error instanceof Error ? error.message : '剧本版本加载失败', 'error')
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episode?.id])

  const startRevision = () => {
    const payload = current?.structured_payload
    setObjective(payload?.episode_objective || episode?.objective || '')
    setHook(payload?.hook || episode?.hook || '')
    setConflict(payload?.conflict || episode?.conflict || '')
    setPayoff(payload?.payoff || episode?.payoff || '')
    setCliffhanger(payload?.cliffhanger || episode?.cliffhanger || '')
    setScenesText(JSON.stringify(payload?.scenes || JSON.parse(EMPTY_SCENES), null, 2))
    setEditing(true)
  }

  const save = async () => {
    if (!episode || busy) return
    let scenes: Array<Record<string, unknown>>
    try {
      const parsed = JSON.parse(scenesText) as unknown
      if (!Array.isArray(parsed) || parsed.length === 0) throw new Error('至少需要一个场景')
      scenes = parsed as Array<Record<string, unknown>>
    } catch (error) {
      toast(error instanceof Error ? `场景 JSON 无效：${error.message}` : '场景 JSON 无效', 'error')
      return
    }
    setBusy(true)
    try {
      await seriesApi.createScriptRevision(episode.id, {
        structured_payload: {
          episode_objective: objective.trim(),
          hook: hook.trim(),
          conflict: conflict.trim(),
          payoff: payoff.trim(),
          cliffhanger: cliffhanger.trim(),
          scenes,
        },
        source_type: 'user',
        based_on_revision: current?.revision,
      })
      toast('已创建新的不可变剧本版本', 'success')
      setEditing(false)
      await load()
      onChanged()
    } catch (error) {
      toast(error instanceof Error ? error.message : '剧本保存失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  const approve = async () => {
    if (!episode || !current || busy) return
    setBusy(true)
    try {
      await seriesApi.submitApproval({
        project_id: projectId,
        target_type: 'script',
        target_id: episode.id,
        target_revision: current.revision,
        action: 'approve',
      })
      toast('剧本已通过审稿', 'success')
      await load()
      onChanged()
    } catch (error) {
      toast(error instanceof Error ? error.message : '审批失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  if (episodes.length === 0) return <div className="studio__muted">项目还没有剧集。</div>

  return (
    <section>
      <div className="sb-episode-tabs">
        {episodes.map((item, index) => (
          <button
            key={item.id}
            className={`studio__btn studio__btn--sm${index === episodeIndex ? ' studio__btn--primary' : ''}`}
            onClick={() => setEpisodeIndex(index)}
          >
            第 {item.index} 集{item.title ? ` · ${item.title}` : ''}
          </button>
        ))}
        <span style={{ flex: 1 }} />
        <button className="studio__btn studio__btn--sm" onClick={startRevision}>
          {current ? '基于当前版修订' : '创建剧本'}
        </button>
      </div>

      {editing ? (
        <div className="studio__card script-editor">
          {[
            ['本集目标', objective, setObjective],
            ['开场 Hook', hook, setHook],
            ['核心冲突', conflict, setConflict],
            ['回报 / 反转', payoff, setPayoff],
            ['结尾悬念', cliffhanger, setCliffhanger],
          ].map(([label, value, setter]) => (
            <label key={label as string} className="sb-shot__field">
              <span>{label as string}</span>
              <textarea
                className="studio__textarea"
                rows={2}
                value={value as string}
                onChange={(event) => (setter as (value: string) => void)(event.target.value)}
              />
            </label>
          ))}
          <label className="sb-shot__field">
            <span>场景结构（JSON）</span>
            <textarea className="studio__textarea script-editor__json" rows={16} value={scenesText} onChange={(e) => setScenesText(e.target.value)} />
          </label>
          <div className="script-editor__actions">
            <button className="studio__btn studio__btn--primary" disabled={busy} onClick={save}>保存为新版本</button>
            <button className="studio__btn" disabled={busy} onClick={() => setEditing(false)}>取消</button>
          </div>
        </div>
      ) : current ? (
        <div className="studio__card">
          <div className="sb-shot__head">
            <b>剧本 revision {current.revision}</b>
            <span className={`studio__badge studio__badge--${current.status}`}>{current.status}</span>
            <span className="studio__muted">来源：{current.source_type}</span>
          </div>
          <dl className="script-summary">
            <dt>目标</dt><dd>{current.structured_payload.episode_objective}</dd>
            <dt>Hook</dt><dd>{current.structured_payload.hook}</dd>
            <dt>冲突</dt><dd>{current.structured_payload.conflict}</dd>
            <dt>悬念</dt><dd>{current.structured_payload.cliffhanger}</dd>
            <dt>场景</dt><dd>{current.structured_payload.scenes.length} 场</dd>
          </dl>
          {current.status === 'draft' && (
            <button className="studio__btn studio__btn--primary studio__btn--sm" disabled={busy} onClick={approve}>
              通过剧本审稿
            </button>
          )}
          {revisions.length > 1 && <div className="studio__muted" style={{ marginTop: 12 }}>历史版本：{revisions.slice(1).map((item) => `rev ${item.revision} (${item.status})`).join('、')}</div>}
        </div>
      ) : (
        <div className="studio__muted">当前集还没有结构化剧本。先创建剧本，再进入导演分镜。</div>
      )}
    </section>
  )
}
