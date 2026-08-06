import { useMemo, useState } from 'react'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { SeriesProject, StoryboardData, StoryboardShot } from '../../api/series'
import './studio.css'

const ASPECT_RATIOS = ['16:9', '9:16', '1:1', '4:3', '2.35:1']

function buildPayload(ep: NonNullable<StoryboardData['episodes']>[number]): Record<string, unknown> {
  return {
    index: ep.index,
    title: ep.title,
    scenes: (ep.scenes || []).map((s) => ({
      index: s.index,
      title: s.title,
      description: s.description,
      shots: (s.shots || []).map((sh) => ({
        index: sh.index,
        duration_ms: sh.duration_ms,
        visual_prompt: sh.visual_prompt,
        camera: sh.camera,
        transition: sh.transition,
        aspect_ratio: sh.aspect_ratio,
        status: sh.status,
        dialogue_tracks: sh.dialogue_tracks,
        narration_tracks: sh.narration_tracks,
        subtitle_tracks: sh.subtitle_tracks,
      })),
    })),
  }
}

function ShotCard({ shot, onSaved }: { shot: StoryboardShot; onSaved: () => void }) {
  const [prompt, setPrompt] = useState(shot.visual_prompt || '')
  const [durationMs, setDurationMs] = useState(shot.duration_ms != null ? String(shot.duration_ms) : '')
  const [aspect, setAspect] = useState(shot.aspect_ratio || '16:9')
  const [saving, setSaving] = useState(false)

  const dirty =
    prompt !== (shot.visual_prompt || '') ||
    durationMs !== (shot.duration_ms != null ? String(shot.duration_ms) : '') ||
    aspect !== (shot.aspect_ratio || '16:9')

  const save = async () => {
    setSaving(true)
    try {
      await seriesApi.patchShot(shot.id, {
        visual_prompt: prompt,
        duration_ms: durationMs ? Number(durationMs) : undefined,
        aspect_ratio: aspect,
      })
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
        {shot.camera && <span className="studio__badge">{shot.camera}</span>}
        {shot.transition && <span className="studio__badge">转场: {shot.transition}</span>}
      </div>
      <div className="sb-shot__field">
        <label>画面描述（visual_prompt）</label>
        <textarea className="studio__textarea" rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      </div>
      <div className="sb-shot__row">
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
              <ShotCard key={shot.id} shot={shot} onSaved={onChanged} />
            ))}
          </div>
        ))
      )}
    </section>
  )
}
