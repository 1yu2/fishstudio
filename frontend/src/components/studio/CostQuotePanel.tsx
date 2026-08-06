import { useEffect, useState } from 'react'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { CostQuote, WorkflowRun } from '../../api/series'
import './studio.css'

/** 报价有效期展示：返回剩余分钟文案；过期返回 null。 */
function expiryLabel(expiresAt?: string): { text: string; expired: boolean } | null {
  if (!expiresAt) return null
  const ms = new Date(expiresAt).getTime() - Date.now()
  if (Number.isNaN(ms)) return null
  if (ms <= 0) return { text: '报价已过期', expired: true }
  const minutes = Math.ceil(ms / 60000)
  return { text: `报价 ${minutes} 分钟后过期`, expired: false }
}

/**
 * 费用确认（复审 0.6 / P2-6）：拉取 cost-quote 明细 → 绑定 quote_id 确认后开始生成。
 * 报价 30 分钟有效；409（quote 不匹配/过期/价格表或分镜已变化）时展示后端具体原因
 * 并自动重新拉取报价。
 */
export default function CostQuotePanel({
  run,
  onConfirmed,
}: {
  run: WorkflowRun
  onConfirmed?: () => void
}) {
  const [quote, setQuote] = useState<CostQuote | null>(null)
  const [loading, setLoading] = useState(false)
  const [confirming, setConfirming] = useState(false)
  // 每分钟刷新一次倒计时显示
  const [, setTick] = useState(0)

  useEffect(() => {
    if (!quote?.expires_at) return
    const timer = window.setInterval(() => setTick((t) => t + 1), 30000)
    return () => window.clearInterval(timer)
  }, [quote?.expires_at])

  const fetchQuote = async () => {
    if (loading) return
    setLoading(true)
    try {
      setQuote(await seriesApi.getCostQuote(run.id))
    } catch (e) {
      toast(e instanceof Error ? e.message : '获取报价失败', 'error')
    } finally {
      setLoading(false)
    }
  }

  const confirm = async () => {
    if (confirming || !quote) return
    setConfirming(true)
    try {
      await seriesApi.confirmCost(run.id, quote.quote_id)
      toast('费用已确认，开始生成', 'success')
      onConfirmed?.()
    } catch (e) {
      const status = (e as { status?: number }).status
      if (status === 409) {
        // 报价失效：展示后端具体原因（过期/不匹配/价格表或分镜变化），并自动重新拉取
        toast(`${e instanceof Error ? e.message : '报价已失效'}，已重新获取最新报价`, 'error')
        setQuote(null)
        void fetchQuote()
      } else {
        toast(e instanceof Error ? e.message : '确认失败', 'error')
      }
    } finally {
      setConfirming(false)
    }
  }

  const expiry = expiryLabel(quote?.expires_at)

  return (
    <div className="quote-panel">
      {!quote ? (
        <button className="studio__btn studio__btn--sm" onClick={fetchQuote} disabled={loading}>
          {loading ? '获取中…' : '获取费用报价'}
        </button>
      ) : (
        <>
          <table className="job-table quote-table">
            <thead>
              <tr>
                <th>步骤</th>
                <th>Provider</th>
                <th>数量</th>
                <th>单价（CNY）</th>
                <th>小计（CNY）</th>
              </tr>
            </thead>
            <tbody>
              {quote.items.map((it, i) => (
                <tr key={`${it.step_node_key}-${i}`}>
                  <td>{it.step_node_key}</td>
                  <td className="mono small">{it.provider && it.model ? `${it.provider}/${it.model}` : it.operation}</td>
                  <td>{it.unit_count}</td>
                  <td>{Number(it.unit_price).toFixed(4)}</td>
                  <td>{Number(it.subtotal).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="quote-panel__footer">
            <span className="studio__muted">价格表版本：{quote.price_table_version}</span>
            {expiry && (
              <span className={expiry.expired ? 'studio__error' : 'studio__muted'}>{expiry.text}</span>
            )}
            <b>总价：¥ {Number(quote.total).toFixed(2)}</b>
            <button className="studio__btn studio__btn--sm" onClick={fetchQuote} disabled={loading}>
              重新报价
            </button>
            <button
              className="studio__btn studio__btn--primary studio__btn--sm"
              onClick={confirm}
              disabled={confirming || Boolean(expiry?.expired)}
            >
              {confirming ? '确认中…' : '确认并开始生成'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
