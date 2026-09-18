import { useMemo, useState } from 'react'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { SeriesProject, StoryboardData, StoryboardShot } from '../../api/series'
import './studio.css'

const ASPECT_RATIOS = ['16:9', '9:16', '1:1', '4:3', '2.35:1']

function buildPayload(ep: NonNullable<StoryboardData['episodes']>[number]): Record<string, unknown> {
  return {
    episodes: [
      {
        episode_id: ep.id,
        number: ep.index,
        title: ep.title,
        scenes: (ep.scenes || []).map((s) => ({
          scene_id: s.id,
          number: s.index,
          location: s.title,
          description: s.description,
          shots: (s.shots || []).map((sh) => ({
            shot_id: sh.id,
            number: sh.index,
            duration_ms: sh.duration_ms,
            shot_type: sh.shot_type,
            narrative_purpose: sh.narrative_purpose,
            composition: sh.composition,
            action_start: sh.action_start,
            action_end: sh.action_end,
            emotion: sh.emotion,
            first_frame_prompt: sh.first_frame_prompt,
            last_frame_prompt: sh.last_frame_prompt,
            generation_mode: sh.generation_mode,
            continuity_constraints: sh.continuity_constraints,
            risk_tags: sh.risk_tags,
            locked_asset_versions: sh.locked_asset_versions,
            qa_status: sh.qa_status,
            visual_prompt: sh.visual_prompt,
            camera: sh.camera,
            transition: sh.transition,
            aspect_ratio: sh.aspect_ratio,
            dialogue_tracks: sh.dialogue_tracks,
            narration_tracks: sh.narration_tracks,
            subtitle_tracks: sh.subtitle_tracks,
          })),
        })),
      },
    ],
  }
}

function ShotCard({ projectId, shot, onSaved }: { projectId: string; shot: StoryboardShot; onSaved: () => void }) {
  const [prompt, setPrompt] = useState(shot.visual_prompt || '')
  const [purpose, setPurpose] = useState(shot.narrative_purpose || '')
  const [emotion, setEmotion] = useState(shot.emotion || '')
  const [shotType, setShotType] = useState(shot.shot_type || '')
  const [generationMode, setGenerationMode] = useState(shot.generation_mode || 'text')
  const [durationMs, setDurationMs] = useState(shot.duration_ms != null ? String(shot.duration_ms) : '')
  const [aspect, setAspect] = useState(shot.aspect_ratio || '16:9')
  const [saving, setSaving] = useState(false)

  const dirty =
    prompt !== (shot.visual_prompt || '') ||
    purpose !== (shot.narrative_purpose || '') ||
    emotion !== (shot.emotion || '') ||
    shotType !== (shot.shot_type || '') ||
    generationMode !== (shot.generation_mode || 'text') ||
    durationMs !== (shot.duration_ms != null ? String(shot.duration_ms) : '') ||
    aspect !== (shot.aspect_ratio || '16:9')

  const save = async () => {
    setSaving(true)
    try {
      const patch: seriesApi.ShotPatch = {
        visual_prompt: prompt,
        narrative_purpose: purpose,
        emotion,
        shot_type: shotType,
        generation_mode: generationMode,
        duration_ms: durationMs ? Number(durationMs) : undefined,
        aspect_ratio: aspect,
      }
      const changedFields = Object.entries(patch)
        .filter(([key, value]) => value !== shot[key])
        .map(([key]) => key)
      const preview = await seriesApi.previewInvalidation(projectId, {
        source_type: 'shot',
        source_id: shot.id,
        changed_fields: changedFields,
        reason: 'shot_content_changed',
      })
      if (
        preview.affected_count > 0 &&
        !window.confirm(
          `本次修改将使 ${preview.affected_count} 个下游对象失效，涉及：${preview.stages.join('、')}。是否继续？`,
        )
      ) return
      await seriesApi.patchShot(shot.id, patch)
      toast('镜头已保存', 'success')
      onSaved()
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  const trackCount =
    (shot.dialogue_tracks?.length || 0) + (shot.narration_tracks?.length || 0) + (shot.subtitle_tracks?.length || 0)

  return (
    <div className="sb-shot">
      <div className="sb-shot__head">
        <b>镜头 {shot.index}</b>
        {shot.status && <span className={`studio__badge studio__badge--${shot.status}`}>{shot.status}</span>}
        {shot.camera?.type != null && <span className="studio__badge">{String(shot.camera.type)}</span>}
        {shot.transition?.type != null && <span className="studio__badge">转场: {String(shot.transition.type)}</span>}
        {shot.qa_status && <span className={`studio__badge studio__badge--${shot.qa_status}`}>QA: {shot.qa_status}</span>}
      </div>
      <div className="sb-shot__field">
        <label>叙事目的</label>
        <input className="studio__input" value={purpose} onChange={(e) => setPurpose(e.target.value)} />
      </div>
      <div className="sb-shot__field" style={{ marginTop: 8 }}>
        <label>画面描述（visual_prompt）</label>
        <textarea className="studio__textarea" rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      </div>
      <div className="sb-shot__row">
        <div className="sb-shot__field" style={{ maxWidth: 160 }}>
          <label>景别 / 镜头类型</label>
          <input className="studio__input" value={shotType} onChange={(e) => setShotType(e.target.value)} placeholder="近景 / 特写" />
        </div>
        <div className="sb-shot__field" style={{ maxWidth: 180 }}>
          <label>目标情绪</label>
          <input className="studio__input" value={emotion} onChange={(e) => setEmotion(e.target.value)} placeholder="紧张、释然…" />
        </div>
        <div className="sb-shot__field" style={{ maxWidth: 180 }}>
          <label>生成模式</label>
          <select className="studio__select" value={generationMode} onChange={(e) => setGenerationMode(e.target.value)}>
            <option value="text">文生图 / 视频</option>
            <option value="first_frame">首帧图生视频</option>
            <option value="first_last_frame">首尾帧</option>
            <option value="lip_sync">口型驱动</option>
          </select>
        </div>
        <div className="sb-shot__field" style={{ maxWidth: 140 }}>
          <label>时长（ms）</label>
          <input
            className="studio__input"
            type="number"
            min={0}
            value={durationMs}
            onChange={(e) => setDurationMs(e.target.value)}
          />
        </div>
        <div className="sb-shot__field" style={{ maxWidth: 140 }}>
          <label>画幅</label>
          <select className="studio__select" value={aspect} onChange={(e) => setAspect(e.target.value)}>
            {ASPECT_RATIOS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-end' }}>
          <button className="studio__btn studio__btn--sm" onClick={save} disabled={saving || !dirty}>
            保存镜头
          </button>
        </div>
      </div>
      {trackCount > 0 && <div className="sb-shot__tracks">台词/旁白/字幕轨道共 {trackCount} 条</div>}
    </div>
  )
}

export default function StoryboardPanel({
  project,
  storyboard,
  onChanged,
}: {
  project: SeriesProject
  storyboard: StoryboardData | null
  onChanged: () => void
}) {
  const episodes = useMemo(
    () => (storyboard?.episodes?.length ? storyboard.episodes : []),
    [storyboard],
  )
  const [episodeIdx, setEpisodeIdx] = useState(0)
  const [importText, setImportText] = useState('')
  const [showImport, setShowImport] = useState(false)
  const [busy, setBusy] = useState(false)

  // 优先使用分镜结构中的 episode，其次项目详情中的 episode 元数据
  const ep = episodes[episodeIdx] || null
  const episodeId = ep?.id || project.episodes?.[episodeIdx]?.id || project.episodes?.[0]?.id

  const doImport = async () => {
    if (!episodeId || !importText.trim() || busy) return
    setBusy(true)
    try {
      await seriesApi.importStoryboardText(episodeId, importText.trim())
      toast('导入成功', 'success')
      setImportText('')
      setShowImport(false)
      onChanged()
    } catch (e) {
      toast(e instanceof Error ? e.message : '导入失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  const saveWhole = async () => {
    if (!episodeId || !ep || busy) return
    setBusy(true)
    try {
      await seriesApi.saveStoryboard(episodeId, buildPayload(ep))
      toast('整版分镜已保存为新 revision', 'success')
      onChanged()
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <div className="sb-episode-tabs">
        {(episodes.length ? episodes : (project.episodes || []).map((e) => ({ id: e.id, index: e.index, title: e.title, scenes: [] }))).map(
          (e, i) => (
            <button
              key={e.id}
              className={`studio__btn studio__btn--sm${i === episodeIdx ? ' studio__btn--primary' : ''}`}
              onClick={() => setEpisodeIdx(i)}
            >
              第 {e.index} 集{e.title ? ` · ${e.title}` : ''}
            </button>
          ),
        )}
        <span style={{ flex: 1 }} />
        <button className="studio__btn studio__btn--sm" onClick={() => setShowImport((v) => !v)}>
          导入旧文本分镜
        </button>
        <button className="studio__btn studio__btn--sm" onClick={saveWhole} disabled={busy || !ep}>
          保存整版分镜
        </button>
      </div>

      {showImport && (
        <div className="studio__card" style={{ marginBottom: 16 }}>
          <div className="studio__muted" style={{ marginBottom: 8 }}>
            粘贴旧版文本分镜（如「镜头 1（3s）【远景】：…」），导入为结构化分镜。
          </div>
          <textarea
            className="studio__textarea"
            rows={8}
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            placeholder={'镜头 1（3s）【远景】：夜色中的便利店…\n镜头 2（2s）【特写】：门被推开…'}
          />
          <div style={{ marginTop: 8 }}>
            <button className="studio__btn studio__btn--primary studio__btn--sm" onClick={doImport} disabled={busy || !importText.trim()}>
              导入
            </button>
          </div>
        </div>
      )}

      {!ep || (ep.scenes || []).length === 0 ? (
        <div className="studio__muted">当前集暂无分镜内容，可通过「导入旧文本分镜」创建。</div>
      ) : (
        (ep.scenes || []).map((scene) => (
          <div key={scene.id} className="sb-scene">
            <div className="sb-scene__title">
              场景 {scene.index}
              {scene.title ? ` · ${scene.title}` : ''}
            </div>
            {(scene.shots || []).map((shot) => (
              <ShotCard key={shot.id} projectId={project.id} shot={shot} onSaved={onChanged} />
            ))}
          </div>
        ))
      )}
    </section>
  )
}
