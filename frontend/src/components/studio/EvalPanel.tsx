import { useEffect, useState } from 'react'
import * as seriesApi from '../../api/series'
import type { EvalRoutingStat } from '../../api/series'
import { toast } from '../ui/Toast'

export default function EvalPanel({ projectId }: { projectId: string }) {
  const [stats, setStats] = useState<EvalRoutingStat[]>([])
  const [feedback, setFeedback] = useState<Record<string, unknown>>({})
  useEffect(() => {
    void Promise.all([seriesApi.getEvalRoutingStats(), seriesApi.getProjectEvalFeedback(projectId)])
      .then(([routing, project]) => { setStats(routing); setFeedback(project) })
      .catch((error) => toast(error instanceof Error ? error.message : 'Eval 加载失败', 'error'))
  }, [projectId])
  return <>
    <section className="studio__card">
      <div className="studio__section-title" style={{ marginTop: 0 }}>项目真实反馈</div>
      <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(feedback, null, 2)}</pre>
    </section>
    <div className="studio__section-title">Deployment 排序依据</div>
    {stats.length === 0 ? <div className="studio__muted">尚无 benchmark 报告。</div> : stats.map((item, index) => <section key={item.deployment} className="studio__card">
      <b>#{index + 1} {item.deployment}</b>
      <p className="studio__muted">{item.provider}/{item.model} · 质量 {item.quality_score} · 健康度 {item.health_score} · P95 {item.p95_latency_ms ?? 'unavailable'}ms · 单位质量成本 {item.unit_quality_cost_cny}</p>
      <p className="studio__muted">主要失败：{item.main_failure_reasons.map(([reason, count]) => `${reason}(${count})`).join('、') || '无'}</p>
    </section>)}
  </>
}
