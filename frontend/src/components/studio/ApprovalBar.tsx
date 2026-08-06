import { useEffect, useState } from 'react'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { ApprovalRecord, GenerationGate, SeriesAsset, StoryboardData } from '../../api/series'
import './studio.css'

/** 从分镜结构收集待审批的 revision 项 */
function pendingStoryboardItems(storyboard: StoryboardData | null): Array<{ target_id: string; revision: number; label: string }> {
  if (!storyboard?.episodes) return []
  return storyboard.episodes
    .filter((ep) => (ep.scenes || []).length > 0)
    .map((ep) => ({
      target_id: ep.id,
      revision: ep.revision ?? 1,
      label: `分镜 · 第 ${ep.index} 集${ep.title ? ` ${ep.title}` : ''} (rev ${ep.revision ?? 1})`,
    }))
}

/** 从资产收集待审批的版本项（preview_ready 状态视为待审批） */
function pendingAssetItems(assets: SeriesAsset[]): Array<{ target_id: string; revision: number; label: string }> {
  return assets
    .filter((a) => a.status === 'preview_ready')
    .map((a) => ({
      target_id: a.id,
      revision: a.version ?? a.current_version ?? 1,
      label: `资产 · ${a.name} (v${a.version ?? a.current_version ?? 1})`,
    }))
}

function ApprovalRow({
  item,
  projectId,
  targetType,
  onDone,
}: {
  item: { target_id: string; revision: number; label: string }
  projectId: string
  targetType: 'storyboard' | 'asset'
  onDone: () => void
}) {
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (action: 'approve' | 'reject') => {
    if (busy) return
    setBusy(true)
    try {
      await seriesApi.submitApproval({
        project_id: projectId,
        target_type: targetType,
        target_id: item.target_id,
        target_revision: item.revision,
        action,
        comment: comment.trim() || undefined,
      })
      toast(action === 'approve' ? '已通过' : '已驳回', 'success')
      onDone()
    } catch (e) {
      toast(e instanceof Error ? e.message : '操作失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="approval-item">
      <span style={{ fontSize: 13 }}>{item.label}</span>
      <input
        className="studio__input approval-item__comment"
        placeholder="审批意见（可选）"
        value={comment}
        onChange={(e) => setComment(e.target.value)}
      />
      <button className="studio__btn studio__btn--primary studio__btn--sm" onClick={() => submit('approve')} disabled={busy}>
        通过
      </button>
      <button className="studio__btn studio__btn--danger studio__btn--sm" onClick={() => submit('reject')} disabled={busy}>
        驳回
      </button>
    </div>
  )
}

export default function ApprovalBar({
  projectId,
  storyboard,
  assets,
  gate,
  onChanged,
}: {
  projectId: string
  storyboard: StoryboardData | null
  assets: SeriesAsset[]
  gate: GenerationGate | null
  onChanged: () => void
}) {
  const [records, setRecords] = useState<ApprovalRecord[]>([])

  useEffect(() => {
    const load = async () => {
      try {
        const list = await seriesApi.listApprovals(projectId)
        setRecords(Array.isArray(list) ? list : [])
      } catch (e) {
        console.error('审批记录加载失败', e)
      }
    }
    void load()
  }, [projectId])

  const sbItems = pendingStoryboardItems(storyboard)
  const assetItems = pendingAssetItems(assets)

  return (
    <section className="studio__card">
      <div className="studio__section-title" style={{ marginTop: 0 }}>
        待审批
      </div>
      {sbItems.length === 0 && assetItems.length === 0 && <div className="studio__muted">暂无待审批项。</div>}
      {sbItems.map((item) => (
        <ApprovalRow
          key={`sb:${item.target_id}:${item.revision}`}
          item={item}
          projectId={projectId}
          targetType="storyboard"
          onDone={onChanged}
        />
      ))}
      {assetItems.map((item) => (
        <ApprovalRow
          key={`asset:${item.target_id}:${item.revision}`}
          item={item}
          projectId={projectId}
          targetType="asset"
          onDone={onChanged}
        />
      ))}

      {gate && (
        <div className={`gate-box ${gate.ok ? 'gate-box--ok' : 'gate-box--blocked'}`}>
          {gate.ok ? (
            '生成门禁：通过，可以开始生成。'
          ) : (
            <>
              生成门禁：未通过，需先处理以下问题：
              <ul>
                {(gate.reasons || []).map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {records.length > 0 && (
        <>
          <div className="studio__section-title">审批历史</div>
          {records.slice(0, 10).map((r) => (
            <div key={r.id} className="studio__muted" style={{ padding: '2px 0' }}>
              [{r.target_type}] {r.action || r.status || '-'}
              {r.comment ? ` · ${r.comment}` : ''}
              {r.created_at ? ` · ${new Date(r.created_at).toLocaleString()}` : ''}
            </div>
          ))}
        </>
      )}
    </section>
  )
}
