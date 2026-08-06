import { Fragment, useState } from 'react'
import type { Job, StoryboardData, WorkflowRun } from '../../api/series'
import { ProtectedImage, ProtectedVideo } from '../common/ProtectedMedia'
import CostQuotePanel from './CostQuotePanel'
import ShotCandidates from './ShotCandidates'
import './studio.css'

function storageUrl(storageKey: string): string {
  return storageKey.startsWith('/storage/') || storageKey.startsWith('http') ? storageKey : `/storage/${storageKey}`
}

/** 产物地址：优先 job.result.url（受保护下载接口），老数据 fallback /storage/... */
function jobMediaUrl(job: Job): string | undefined {
  const url = job.result?.url
  if (url) return url
  const storageKey = job.result?.storage_key
  return storageKey ? storageUrl(storageKey) : undefined
}

/** 从 storyboard 数据中取 Shot 级独立选择槽。 */
function shotSelectedImageId(storyboard: StoryboardData | null | undefined, shotId: string): string | null {
  for (const ep of storyboard?.episodes || []) {
    for (const scene of ep.scenes || []) {
      const shot = (scene.shots || []).find((s) => s.id === shotId)
      if (shot) return shot.selected_image_artifact_id ?? null
    }
  }
  return null
}

function shotSelectedVideoId(storyboard: StoryboardData | null | undefined, shotId: string): string | null {
  for (const ep of storyboard?.episodes || []) {
    for (const scene of ep.scenes || []) {
      const shot = (scene.shots || []).find((s) => s.id === shotId)
      if (shot) return shot.selected_video_artifact_id ?? null
    }
  }
  return null
}

export default function GeneratePanel({
  run,
  jobs,
  storyboard,
  onRetryJob,
  onCancelJob,
  onChanged,
  resultsOnly = false,
}: {
  run: WorkflowRun | null
  jobs: Job[]
  /** storyboard 数据：用于候选产物的 Shot.selected_artifact_id 已选定标记 */
  storyboard?: StoryboardData | null
  onRetryJob?: (jobId: string) => void
  onCancelJob?: (jobId: string) => void
  onChanged?: () => void
  resultsOnly?: boolean
}) {
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null)

  // cost_confirmed 记录在 run.input_snapshot.cost_confirmed
  const costConfirmed = Boolean((run?.input_snapshot as { cost_confirmed?: boolean } | undefined)?.cost_confirmed)
  const waitingCostConfirm = !resultsOnly && Boolean(run) && !costConfirmed

  return (
    <section>
      {!resultsOnly && (
        <div className="cost-banner">
          <div>
            <div className="studio__muted">预估费用</div>
            <div className="cost-banner__value">
              ¥ {run?.estimated_cost != null ? Number(run.estimated_cost).toFixed(2) : '--'}
              <span className="studio__muted" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
                （CNY，按镜头与模型估算）
              </span>
            </div>
            {run?.cost_limit != null && <div className="studio__muted">费用上限：¥ {Number(run.cost_limit).toFixed(2)}</div>}
          </div>
          <div className="studio__muted" style={{ maxWidth: 420 }}>
            生成流程：审批通过 → 确认费用 → 后端按 workflow DAG 逐镜头排队生成。任务进度在下方实时刷新，失败任务可单独重试。
          </div>
        </div>
      )}

      {!resultsOnly && run && (
        <div className="studio__card" style={{ marginBottom: 16 }}>
          <div className="studio__section-title" style={{ marginTop: 0 }}>
            费用确认
          </div>
          {costConfirmed ? (
            <div className="studio__muted">费用已确认，生成流程进行中。</div>
          ) : (
            <CostQuotePanel run={run} onConfirmed={onChanged} />
          )}
        </div>
      )}

      {waitingCostConfirm && (
        <div className="studio__card studio__muted" style={{ marginBottom: 16 }}>
          等待费用确认：确认报价后任务才会开始排队生成。
        </div>
      )}

      {jobs.length === 0 ? (
        <div className="studio__muted">{waitingCostConfirm ? '暂无任务（等待费用确认）。' : '暂无任务。'}</div>
      ) : (
        <table className="job-table">
          <thead>
            <tr>
              <th>操作</th>
              <th>Provider / Model</th>
              <th>状态</th>
              <th>进度</th>
              <th>尝试</th>
              <th>错误</th>
              {!resultsOnly && <th>操作</th>}
              <th>产物</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => {
              const mediaUrl = jobMediaUrl(j)
              // 后端 progress 已是 0-100，直接使用
              const progress = Math.round(j.progress ?? 0)
              const expandable =
                !resultsOnly &&
                (j.operation === 'generate_video' || j.operation === 'generate_image') &&
                Boolean(j.shot_id)
              const expanded = expandedJobId === j.id
              return (
                <Fragment key={j.id}>
                  <tr>
                    <td>{j.operation || '-'}</td>
                    <td>
                      {j.provider || '-'}
                      {j.model ? ` / ${j.model}` : ''}
                    </td>
                    <td>
                      <span className={`studio__badge studio__badge--${j.status}`}>{j.status}</span>
                    </td>
                    <td>
                      <div className="job-progress" title={`${progress}%`}>
                        <div className="job-progress__bar" style={{ width: `${progress}%` }} />
                      </div>
                    </td>
                    <td>{j.attempt ?? '-'}</td>
                    <td style={{ maxWidth: 180, wordBreak: 'break-all' }}>{j.error || ''}</td>
                    {!resultsOnly && (
                      <td>
                        {j.status === 'failed' && onRetryJob && (
                          <button className="studio__btn studio__btn--sm" onClick={() => onRetryJob(j.id)}>
                            重试
                          </button>
                        )}
                        {(j.status === 'running' || j.status === 'pending' || j.status === 'leased') && onCancelJob && (
                          <button className="studio__btn studio__btn--danger studio__btn--sm" onClick={() => onCancelJob(j.id)}>
                            取消
                          </button>
                        )}
                        {expandable && (
                          <button
                            className="studio__btn studio__btn--sm"
                            onClick={() => setExpandedJobId(expanded ? null : j.id)}
                          >
                            {expanded ? '收起候选' : '候选产物'}
                          </button>
                        )}
                      </td>
                    )}
                    <td>
                      {mediaUrl && j.operation === 'generate_image' ? (
                        <ProtectedImage className="job-result-media" src={mediaUrl} alt="生成结果" />
                      ) : mediaUrl ? (
                        <ProtectedVideo className="job-result-video" src={mediaUrl} controls preload="metadata" />
                      ) : (
                        ''
                      )}
                    </td>
                  </tr>
                      {expanded && j.shot_id && (
                        <tr className="job-candidates-row">
                          <td colSpan={resultsOnly ? 7 : 8}>
                            <ShotCandidates
                              shotId={j.shot_id}
                              operation={j.operation as 'generate_video' | 'generate_image'}
                              selectedArtifactId={
                                j.operation === 'generate_image'
                                  ? shotSelectedImageId(storyboard, j.shot_id)
                                  : shotSelectedVideoId(storyboard, j.shot_id)
                              }
                              onChanged={onChanged}
                            />
                          </td>
                        </tr>
                      )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}
