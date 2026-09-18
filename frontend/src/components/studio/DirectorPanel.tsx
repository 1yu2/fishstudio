import { useEffect, useState } from 'react'
import * as api from '../../api/series'
import type { DirectorPlanRevision, ObserverReview, ScriptRevision, SeriesEpisode } from '../../api/series'
import { toast } from '../ui/Toast'

const DEFAULT_PLAN = {
  schema_version: 1,
  creative_statement: '',
  pacing_strategy: '',
  hook_strategy: '',
  visual_strategy: '',
  audio_strategy: '',
  intents: [{ key: 'beat-1', scene_number: 1, beat: '', objective: '', continuity_requirements: {} }],
}

export default function DirectorPanel({ episodes, onChanged }: { episodes: SeriesEpisode[]; onChanged: () => void }) {
  const [episodeId, setEpisodeId] = useState(episodes[0]?.id || '')
  const [scripts, setScripts] = useState<ScriptRevision[]>([])
  const [plans, setPlans] = useState<DirectorPlanRevision[]>([])
  const [reviews, setReviews] = useState<ObserverReview[]>([])
  const [text, setText] = useState(JSON.stringify(DEFAULT_PLAN, null, 2))
  const [saving, setSaving] = useState(false)
  const [reviewing, setReviewing] = useState(false)

  const reload = async () => {
    if (!episodeId) return
    const [nextScripts, nextPlans, nextReviews] = await Promise.all([
      api.listScriptRevisions(episodeId), api.listDirectorPlans(episodeId), api.listObserverReviews(episodeId),
    ])
    setScripts(nextScripts); setPlans(nextPlans); setReviews(nextReviews)
    if (nextPlans[0]) setText(JSON.stringify(nextPlans[0].payload, null, 2))
  }
  useEffect(() => { void reload() }, [episodeId])

  const save = async () => {
    const script = scripts[0]
    if (!script) return toast('请先创建剧本版本', 'error')
    try {
      setSaving(true)
      await api.createDirectorPlan(episodeId, {
        based_on_script_revision_id: script.id,
        payload: JSON.parse(text),
        platform_constraints: { platform: 'xiaohongshu', aspect_ratio: '9:16' },
        source_type: 'user',
      })
      toast('导演计划新版本已保存', 'success'); await reload(); onChanged()
    } catch (error) { toast(error instanceof Error ? error.message : '保存失败', 'error') }
    finally { setSaving(false) }
  }

  const runReview = async (review?: ObserverReview, issueCode?: string, category?: string) => {
    const script = scripts[0]
    if (!review && !script) return toast('请先创建剧本版本', 'error')
    try {
      setReviewing(true)
      await api.runObserverReview(episodeId, {
        review_type: review?.review_type || 'script_review',
        target_type: (review?.target_type as 'script' | 'storyboard') || 'script',
        target_id: review?.target_id || script.id,
        target_revision: review?.target_revision || script.revision,
        issue_codes: issueCode ? [issueCode] : [], categories: category ? [category] : [],
      })
      toast(issueCode ? '针对性复审已完成' : 'Observer 复审已完成', 'success'); await reload(); onChanged()
    } catch (error) { toast(error instanceof Error ? error.message : '复审失败', 'error') }
    finally { setReviewing(false) }
  }

  const findings = reviews.flatMap((review) => [...review.deterministic_findings, ...review.specialist_findings, ...review.llm_findings])
  return <section className="studio__card">
    <div className="studio__section-title" style={{ marginTop: 0 }}>导演计划与 Observer</div>
    <select className="studio__input" value={episodeId} onChange={(event) => setEpisodeId(event.target.value)}>
      {episodes.map((episode, index) => <option key={episode.id} value={episode.id}>第 {episode.index || index + 1} 集 · {episode.title || '未命名'}</option>)}
    </select>
    <div className="studio__muted">基于剧本 revision {scripts[0]?.revision || '—'} · 当前导演计划 revision {plans[0]?.revision || '—'}</div>
    <textarea className="studio__textarea" rows={18} value={text} onChange={(event) => setText(event.target.value)} />
    <button className="studio__btn" disabled={saving} onClick={() => void save()}>{saving ? '保存中…' : '保存新 DirectorPlan revision'}</button>
    <div className="studio__section-title">Observer 复审历史</div>
    <button className="studio__btn studio__btn--secondary" disabled={reviewing} onClick={() => void runReview()}>{reviewing ? '模型复审中…' : '运行当前剧本复审'}</button>
    {reviews.length === 0 && <div className="studio__muted">暂无复审记录。</div>}
    {reviews.map((review) => <div className="studio__review" key={review.id}>
      <strong>{review.review_type} · {review.score.toFixed(0)} 分</strong>
      <span className={review.blocking ? 'studio__error' : 'studio__muted'}>{review.blocking ? '阻止自动晋级' : '检查通过'}</span>
      <div className="studio__muted">revision {review.target_revision} · {review.summary || '无摘要'}</div>
      <div className="studio__muted">模型费用：{review.model_cost_cny == null ? '暂不可用' : `¥${review.model_cost_cny.toFixed(4)}`}</div>
    </div>)}
    {findings.map((finding, index) => <div className="studio__review" key={`${finding.code}-${index}`}>
      <strong>{finding.severity.toUpperCase()} · {finding.category}</strong>
      <div>{finding.evidence}</div><div className="studio__muted">建议：{finding.suggested_fix || '—'} · 定位：{JSON.stringify(finding.location)}</div>
      <button className="studio__btn studio__btn--secondary" disabled={reviewing} onClick={() => void runReview(reviews.find((item) => [...item.deterministic_findings, ...item.specialist_findings, ...item.llm_findings].includes(finding)), finding.code, finding.category)}>针对该问题复审</button>
    </div>)}
  </section>
}
