import { useEffect, useState } from 'react'
import * as seriesApi from '../../api/series'
import type { UsageRow } from '../../api/series'
import './studio.css'

export default function UsageSummary({ projectId }: { projectId?: string }) {
  const [rows, setRows] = useState<UsageRow[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const load = async () => {
      try {
        const list = await seriesApi.getUsageSummary(projectId, 'operation')
        setRows(Array.isArray(list) ? list : [])
      } catch (e) {
        setError(e instanceof Error ? e.message : '加载失败')
      }
    }
    void load()
  }, [projectId])

  if (error) return <div className="studio__error">{error}</div>
  if (rows.length === 0) return <div className="studio__muted">暂无费用数据。</div>

  const total = rows.reduce((sum, r) => sum + (Number(r.total_cost) || 0), 0)

  return (
    <table className="usage-table">
      <thead>
        <tr>
          <th>成本类别</th>
          <th>任务数</th>
          <th>输入 / 输出用量</th>
          <th>费用（CNY）</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={r.operation || i}>
            <td>{r.operation || '-'}</td>
            <td>{r.job_count ?? '-'}</td>
            <td>{Number(r.input_units || 0).toLocaleString()} / {Number(r.output_units || 0).toLocaleString()}</td>
            <td>
              ¥ {Number(r.total_cost ?? 0).toFixed(2)}
              {Number(r.unpriced_count || 0) > 0 && (
                <span className="studio__muted"> · {r.unpriced_count} 笔未计价</span>
              )}
            </td>
          </tr>
        ))}
        <tr>
          <td>
            <b>合计</b>
          </td>
          <td />
          <td />
          <td>
            <b>¥ {total.toFixed(2)}</b>
          </td>
        </tr>
      </tbody>
    </table>
  )
}
