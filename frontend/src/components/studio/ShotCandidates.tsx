import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { ArtifactCandidate, ShotArtifact } from '../../api/series'
import { ProtectedImage, ProtectedVideo } from '../common/ProtectedMedia'
import './studio.css'

function StarRating({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <span className="candidate-card__stars">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          className={`candidate-card__star${n <= value ? ' candidate-card__star--on' : ''}`}
          onClick={() => onChange(n)}
          title={`${n} 星`}
        >
          ★
        </button>
      ))}
    </span>
  )
}

function CandidateCard({
  shotId,
  artifact,
  candidate,
  selected,
  operation,
  onChanged,
}: {
  shotId: string
  artifact: ShotArtifact
  candidate?: ArtifactCandidate
  selected: boolean
  operation: 'generate_video' | 'generate_image'
  onChanged: () => void
}) {
  const [score, setScore] = useState(0)
  const [reason, setReason] = useState('wrong_camera')
  const [instruction, setInstruction] = useState('')
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    const sync = (event: Event) => {
      const detail = (event as CustomEvent<{ shotId: string; time: number }>).detail
      if (detail?.shotId === shotId && videoRef.current) videoRef.current.currentTime = detail.time
    }
    window.addEventListener('fishstudio:candidate-seek', sync)
    return () => window.removeEventListener('fishstudio:candidate-seek', sync)
  }, [shotId])

  const stepFrame = (direction: -1 | 1) => {
    const video = videoRef.current
    if (!video) return
    const time = Math.max(0, Math.min(video.duration || Infinity, video.currentTime + direction / 25))
    window.dispatchEvent(new CustomEvent('fishstudio:candidate-seek', { detail: { shotId, time } }))
  }

  const send = async (action: seriesApi.FeedbackAction) => {
    if (busy) return
    if (action === 'rated' && score < 1) {
      toast('请先选择评分（1-5 星）', 'error')
      return
    }
    setBusy(true)
    try {
      // P2-2 新语义：selected 写 Shot.selected_artifact_id（shot 级选择槽），
      // step_id 省略——后端允许空，按 artifact 来源 shot 写入，不再依赖 step 启发式匹配。
      await seriesApi.submitArtifactFeedback(artifact.artifact_id, {
        action,
        score: action === 'rated' ? score : undefined,
        reason: reason.trim() || undefined,
      })
      toast(action === 'selected' ? '已选定该产物' : action === 'rejected' ? '已拒绝该产物' : '评分已提交', 'success')
      onChanged()
    } catch (e) {
      toast(e instanceof Error ? e.message : '操作失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  const lifecycleAction = async (action: 'promote' | 'reject' | 'rate' | 'regenerate') => {
    if (!candidate || busy) return
    if (action === 'rate' && score < 1) return toast('请先选择评分（1-5 星）', 'error')
    setBusy(true)
    try {
      if (action === 'promote') await seriesApi.promoteShotCandidate(shotId, candidate.id)
      if (action === 'reject') await seriesApi.rejectShotCandidate(shotId, candidate.id, reason)
      if (action === 'rate') await seriesApi.evaluateShotCandidate(shotId, candidate.id, {
        score: score / 5,
        findings: candidate.qa_findings,
        evaluated_by: 'human',
      })
      if (action === 'regenerate') {
        const input = {
          reason,
          instruction: instruction.trim(),
          preserve_fields: ['character', 'scene', 'action'],
          replace_fields: [reason === 'wrong_camera' ? 'camera' : reason],
        }
        const quote = await seriesApi.quoteShotCandidateRegeneration(shotId, candidate.id, input)
        if (!window.confirm(`本次局部重拍报价 ${quote.currency} ${Number(quote.price).toFixed(2)}，确认创建任务？`)) return
        await seriesApi.regenerateShotCandidate(shotId, candidate.id, { ...input, confirmed_quote_id: quote.quote_id })
      }
      toast(action === 'regenerate' ? '局部重拍任务已创建' : '候选状态已更新', 'success')
      onChanged()
    } catch (e) {
      toast(e instanceof Error ? e.message : '操作失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`candidate-card${selected ? ' candidate-card--selected' : ''}`}>
      {selected && <span className="studio__badge studio__badge--succeeded candidate-card__badge">已选定</span>}
      {operation === 'generate_image' || artifact.type === 'image' ? (
        <button type="button" className="candidate-card__preview" onClick={() => setExpanded(true)} title="放大预览">
          <ProtectedImage className="candidate-card__image" src={artifact.url} alt="镜头候选图" />
        </button>
      ) : (
        <>
          <ProtectedVideo ref={videoRef} className="candidate-card__video" src={artifact.url} controls preload="metadata" />
          <div className="candidate-card__actions">
            <button type="button" className="studio__btn studio__btn--sm" onClick={() => stepFrame(-1)}>上一帧</button>
            <button type="button" className="studio__btn studio__btn--sm" onClick={() => stepFrame(1)}>下一帧</button>
          </div>
        </>
      )}
      <div className="studio__muted candidate-card__meta">
        {artifact.provider || '-'}{artifact.model ? ` / ${artifact.model}` : ''}
      </div>
      {candidate && (
        <div className="candidate-card__qa">
          <span className={`studio__badge studio__badge--${candidate.status === 'approved' ? 'succeeded' : candidate.status === 'rejected' ? 'failed' : 'pending'}`}>
            {candidate.status}
          </span>
          <span>QA {candidate.score == null ? '--' : `${Math.round(candidate.score * 100)}%`}</span>
          {candidate.qa_findings.map((finding, index) => (
            <span key={index} title={String(finding.detail || '')}>{String(finding.code || '')}</span>
          ))}
        </div>
      )}
      <div className="candidate-card__actions">
        <button className="studio__btn studio__btn--sm" onClick={() => candidate ? lifecycleAction('promote') : send('selected')} disabled={busy || selected}>
          选定
        </button>
        <button className="studio__btn studio__btn--danger studio__btn--sm" onClick={() => candidate ? lifecycleAction('reject') : send('rejected')} disabled={busy}>
          拒绝
        </button>
        {candidate && <button className="studio__btn studio__btn--sm" onClick={() => lifecycleAction('regenerate')} disabled={busy}>局部重拍</button>}
      </div>
      <div className="candidate-card__rate">
        <StarRating value={score} onChange={setScore} />
        <button className="studio__btn studio__btn--sm" onClick={() => candidate ? lifecycleAction('rate') : send('rated')} disabled={busy}>
          评分
        </button>
      </div>
      <select className="studio__input candidate-card__reason" value={reason} onChange={(e) => setReason(e.target.value)}>
        <option value="character_inconsistent">人物不一致</option>
        <option value="wardrobe_inconsistent">服装不一致</option>
        <option value="wrong_character_count">人数错误</option>
        <option value="wrong_action">动作错误</option>
        <option value="wrong_expression">表情错误</option>
        <option value="wrong_camera">机位错误</option>
        <option value="scene_drift">场景漂移</option>
        <option value="style_drift">风格漂移</option>
        <option value="bad_anatomy">肢体异常</option>
        <option value="continuity_failed">连续性失败</option>
        <option value="partial_segment_failed">局部片段失败</option>
        <option value="technical_quality_failed">技术质量失败</option>
      </select>
      {candidate && <input
        className="studio__input candidate-card__reason"
        placeholder="重拍指令，例如：保持人物动作，仅改为低机位"
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
      />}
      {expanded && <div className="candidate-lightbox" role="dialog" aria-modal="true" onClick={() => setExpanded(false)}>
        <ProtectedImage src={artifact.url} alt="候选图放大预览" />
        <button type="button" className="studio__btn" onClick={() => setExpanded(false)}>关闭</button>
      </div>}
    </div>
  )
}

/**
 * 镜头候选产物（P1-3）：按 operation 查询 generate_video / generate_image 候选。
 * 已选定状态按独立选择槽传入（selectedImageArtifactId / selectedVideoArtifactId）。
 */
export default function ShotCandidates({
  shotId,
  selectedArtifactId,
  operation = 'generate_video',
  onChanged,
}: {
  shotId: string
  selectedArtifactId?: string | null
  operation?: 'generate_video' | 'generate_image'
  onChanged?: () => void
}) {
  const [artifacts, setArtifacts] = useState<ShotArtifact[] | null>(null)
  const [candidates, setCandidates] = useState<ArtifactCandidate[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [nextArtifacts, nextCandidates] = await Promise.all([
        seriesApi.listShotArtifacts(shotId, operation),
        seriesApi.listShotCandidates(shotId),
      ])
      setArtifacts(nextArtifacts)
      setCandidates(nextCandidates)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : '候选产物加载失败')
    }
  }, [operation, shotId])

  useEffect(() => {
    void load()
  }, [load])

  const handleChanged = () => {
    void load()
    onChanged?.()
  }

  if (error) return <div className="studio__error">{error}</div>
  if (!artifacts) return <div className="studio__muted">候选产物加载中…</div>
  if (artifacts.length === 0) return <div className="studio__muted">该镜头暂无候选产物。</div>

  return (
    <div className="candidate-strip">
      {artifacts.map((a) => (
        <CandidateCard
          key={a.artifact_id}
          shotId={shotId}
          artifact={a}
          candidate={candidates.find((candidate) => candidate.artifact_id === a.artifact_id)}
          selected={selectedArtifactId === a.artifact_id}
          operation={operation}
          onChanged={handleChanged}
        />
      ))}
    </div>
  )
}
