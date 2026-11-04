import { Loader2, Play, X } from 'lucide-react'
import type { BatchQuote, BatchRunKind, BatchScope } from '../../api/nodeCanvas'
import './nodeCanvas.css'

export interface BatchPlan { scope: BatchScope; nodeId?: string; kinds?: BatchRunKind[]; requestId: string; quote: BatchQuote | null; error: string; notice: string; busy: boolean }

/** 分段批次的称呼：只含图片 = 画面批次，只含视频 = 视频批次，只含配音 = 配音批次，其余 = 批量 */
export const kindsLabel = (kinds?: BatchRunKind[] | null) => {
  if (kinds?.length === 1) return { image: '画面批次', video: '视频批次', tts: '配音批次' }[kinds[0]]
  return '批量'
}
export const dialogTitle = (plan: BatchPlan) => {
  if (plan.kinds?.length === 1) return { image: '生成全部画面', video: '生成全部视频', tts: '生成剩余配音' }[plan.kinds[0]]
  return plan.scope === 'all' ? '全部运行' : '运行此节点及下游'
}

export const money = (value: number) => `¥${value.toFixed(value < 1 ? 3 : 2)}`

/** 批量运行确认：先看合计报价和不能运行 / 价格未知的节点，确认后才提交。 */
export function BatchDialog({ plan, onConfirm, onClose }: { plan: BatchPlan; onConfirm: () => void; onClose: () => void }) {
  const { quote } = plan
  const skipped = quote?.nodes.filter((item) => item.status === 'skipped').length ?? 0
  // 旧版服务端没有这些字段：按原来的口径（待运行节点都算）回退，不误判成“没有可运行的节点”
  const runnable = quote?.runnable_count ?? (quote ? quote.nodes.length - skipped : 0)
  const blocking = quote?.blocking_count ?? 0
  const notes = [
    skipped > 0 ? `${skipped} 个已有结果，跳过` : '',
    quote?.waiting_count ? `${quote.waiting_count} 个缺上游图片，先跳过` : '',
  ].filter(Boolean).join('；')
  const cannotRun = quote?.unavailable_nodes.filter((item) => item.runs === false) ?? []
  const unpriced = quote?.unavailable_nodes.filter((item) => item.runs !== false) ?? []
  return <aside className="nc-copilot nc-batch" aria-label="批量运行">
    <header><Play size={15} /><strong>{dialogTitle(plan)}</strong><button className="nc-icon" aria-label="关闭批量运行" onClick={onClose}><X size={14} /></button></header>
    {!quote && !plan.error && <p className="nc-copilot__hint"><Loader2 size={13} className="nc-spin" /> 正在报价…</p>}
    {plan.notice && <p className="nc-batch__warn" role="status">{plan.notice}</p>}
    {plan.error && <div className="nc-error nc-copilot__error" role="alert">{plan.error}</div>}
    {quote && <div className="nc-copilot__result">
      {blocking > 0
        ? <p className="nc-batch__warn">有 {blocking} 个节点需要先处理，处理后才能运行</p>
        : runnable === 0
          ? <p className="nc-batch__total">没有可以运行的节点{notes ? `（${notes}）` : ''}</p>
          : <p className="nc-batch__total">将运行 {runnable} 个节点{notes ? `（${notes}）` : ''}，预计合计 <b>{money(quote.estimated_total)}</b></p>}
      {quote.budget.remaining !== null && <p className={quote.exceeds_budget ? 'nc-batch__warn' : 'nc-copilot__hint'}>
        今日剩余额度 {money(quote.budget.remaining)}{quote.exceeds_budget ? `，云端预计 ${money(quote.billable_total)}，超出额度无法运行` : ''}
      </p>}
      {cannotRun.length > 0 && <section><h4>这些节点暂时无法运行（未计入合计）</h4>
        {cannotRun.map((item) => <div key={item.node_id} className="nc-copilot__item"><b>{item.label}</b>{item.reason}</div>)}
      </section>}
      {unpriced.length > 0 && <section><h4>价格未知（未计入合计）</h4>
        {unpriced.map((item) => <div key={item.node_id} className="nc-copilot__item"><b>{item.label}</b>{item.reason}</div>)}
      </section>}
      <button className="nc-run nc-run--wide" disabled={plan.busy || quote.exceeds_budget || blocking > 0 || runnable === 0} onClick={onConfirm}>
        {plan.busy ? <Loader2 size={13} className="nc-spin" /> : <Play size={13} />}确认运行
      </button>
    </div>}
  </aside>
}
