import { useCallback, useEffect, useState } from 'react'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { ShotArtifact } from '../../api/series'
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
  artifact,
  selected,
  operation,
  onChanged,
}: {
  artifact: ShotArtifact
  selected: boolean
  operation: 'generate_video' | 'generate_image'
  onChanged: () => void
}) {
  const [score, setScore] = useState(0)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

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

  return (
    <div className={`candidate-card${selected ? ' candidate-card--selected' : ''}`}>
      {selected && <span className="studio__badge studio__badge--succeeded candidate-card__badge">已选定</span>}
      {operation === 'generate_image' || artifact.type === 'image' ? (
        <ProtectedImage className="candidate-card__image" src={artifact.url} alt="镜头候选图" />
      ) : (
        <ProtectedVideo className="candidate-card__video" src={artifact.url} controls preload="metadata" />
      )}
      <div className="studio__muted candidate-card__meta">
        {artifact.provider || '-'}{artifact.model ? ` / ${artifact.model}` : ''}
      </div>
      <div className="candidate-card__actions">
        <button className="studio__btn studio__btn--sm" onClick={() => send('selected')} disabled={busy || selected}>
          选定
        </button>
        <button className="studio__btn studio__btn--danger studio__btn--sm" onClick={() => send('rejected')} disabled={busy}>
          拒绝
        </button>
      </div>
      <div className="candidate-card__rate">
        <StarRating value={score} onChange={setScore} />
        <button className="studio__btn studio__btn--sm" onClick={() => send('rated')} disabled={busy}>
          评分
        </button>
      </div>
      <input
        className="studio__input candidate-card__reason"
        placeholder="反馈原因（可选）"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
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
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setArtifacts(await seriesApi.listShotArtifacts(shotId, operation))
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
          artifact={a}
          selected={selectedArtifactId === a.artifact_id}
          operation={operation}
          onChanged={handleChanged}
        />
      ))}
    </div>
  )
}
