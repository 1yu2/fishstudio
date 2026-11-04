import { AlertTriangle, CheckCircle2, Info, Loader2 } from 'lucide-react'
import type { QcIssue, RenderQc } from '../../api/final'
import { clock } from './timelineLayout'

interface Props {
  /** 正在预览 / 导出的那次渲染的检查结果；undefined / null = 旧渲染没有检查 */
  qc: RenderQc | null | undefined
  /** 是否有成功的渲染可检查 */
  hasRender: boolean
  /** 有渲染任务在跑 */
  rendering: boolean
  onSeek: (seconds: number) => void
}

/** “0:12–0:13”；起止落在同一秒只显示一个时间。 */
export function timeLabel(issue: QcIssue): string | null {
  if (issue.start_ms === undefined) return null
  const start = clock(issue.start_ms)
  const end = clock(issue.end_ms ?? issue.start_ms)
  return start === end ? start : `${start}–${end}`
}

function Issue({ issue, onSeek }: { issue: QcIssue; onSeek: (seconds: number) => void }) {
  const time = timeLabel(issue)
  const blocking = issue.level === 'blocking'
  return (
    <li className={`fn-qc__issue fn-qc__issue--${blocking ? 'blocking' : 'warning'}`}>
      <AlertTriangle size={13} aria-hidden="true" />
      {time && <button type="button" className="fn-qc__time" aria-label={`跳到 ${time}`} onClick={() => onSeek((issue.start_ms ?? 0) / 1000)}>{time}</button>}
      <span>{issue.message}</span>
    </li>
  )
}

/** 成片页的出片检查卡片：渲染中 / 旧渲染 / 通过 / 提醒 / 没通过 / 检查出错。 */
export default function QcCard({ qc, hasRender, rendering, onSeek }: Props) {
  let body: React.ReactNode
  if (rendering || !hasRender) {
    body = <p className="fn-qc__line fn-qc__line--muted">{rendering && <Loader2 size={13} className="fn-spin" aria-hidden="true" />}渲染完成后自动检查</p>
  } else if (!qc) {
    body = <p className="fn-qc__line fn-qc__line--muted"><Info size={13} aria-hidden="true" />这次渲染没有出片检查，重新渲染后可查看</p>
  } else if (qc.status === 'passed') {
    body = (
      <>
        <p className="fn-qc__line fn-qc__line--ok"><CheckCircle2 size={14} aria-hidden="true" />出片检查已通过</p>
        <p className="fn-qc__small">没有黑屏片段 · 没有静音片段 · 声音和画面时长一致</p>
      </>
    )
  } else if (qc.status === 'error') {
    body = <p className="fn-qc__line fn-qc__line--muted"><Info size={13} aria-hidden="true" />{qc.error || '出片检查没有跑完'}（不影响导出）</p>
  } else {
    const blocking = qc.issues.filter((issue) => issue.level === 'blocking')
    const warnings = qc.issues.filter((issue) => issue.level !== 'blocking')
    body = (
      <>
        <p className={`fn-qc__line ${qc.status === 'failed' ? 'fn-qc__line--bad' : 'fn-qc__line--warn'}`}>
          <AlertTriangle size={14} aria-hidden="true" />
          {qc.status === 'failed' ? '出片检查没通过' : `出片检查有 ${qc.issues.length} 条提醒`}
        </p>
        <ul className="fn-qc__list" aria-label="出片检查问题">
          {[...blocking, ...warnings].map((issue, index) => <Issue key={`${issue.code}-${issue.start_ms ?? ''}-${index}`} issue={issue} onSeek={onSeek} />)}
        </ul>
        {qc.status === 'failed' && <p className="fn-qc__small">建议重新渲染；反复出现请联系管理员。</p>}
      </>
    )
  }
  const notes = !rendering && hasRender && qc && qc.status !== 'error' ? qc.notes ?? [] : []
  return (
    <section className="fn-qc" aria-label="出片检查">
      <h3 className="fn-qc__title">出片检查</h3>
      {body}
      {notes.map((note) => <p key={note} className="fn-qc__small">{note}</p>)}
    </section>
  )
}
