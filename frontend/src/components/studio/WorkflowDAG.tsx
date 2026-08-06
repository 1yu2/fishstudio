import { useMemo, useState } from 'react'
import type { WorkflowRun, WorkflowStep } from '../../api/series'
import './studio.css'

const NODE_W = 150
const NODE_H = 64
const GAP_X = 80
const GAP_Y = 24

/**
 * 拓扑分层布局：Kahn 算法求每个节点的层（依赖最大层 +1），
 * x = 层序 * (宽+间距)，y = 层内序号 * (高+间距)。
 */
function layoutDag(steps: WorkflowStep[]): Map<string, { x: number; y: number }> {
  const byKey = new Map(steps.map((s) => [s.node_key, s]))
  const layerCache = new Map<string, number>()
  const layerOf = (key: string, visiting: Set<string>): number => {
    if (layerCache.has(key)) return layerCache.get(key)!
    if (visiting.has(key)) return 0 // 环防御
    visiting.add(key)
    const step = byKey.get(key)
    const deps = (step?.dependencies || []).filter((d) => byKey.has(d))
    const layer = deps.length === 0 ? 0 : Math.max(...deps.map((d) => layerOf(d, visiting))) + 1
    visiting.delete(key)
    layerCache.set(key, layer)
    return layer
  }

  const layers = new Map<number, string[]>()
  for (const s of steps) {
    const l = layerOf(s.node_key, new Set())
    if (!layers.has(l)) layers.set(l, [])
    layers.get(l)!.push(s.node_key)
  }

  const pos = new Map<string, { x: number; y: number }>()
  for (const [layer, keys] of layers) {
    keys.forEach((key, i) => {
      pos.set(key, { x: layer * (NODE_W + GAP_X), y: i * (NODE_H + GAP_Y) })
    })
  }
  return pos
}

const STATUS_COLORS: Record<string, string> = {
  succeeded: '#1a7f37',
  failed: '#d1242f',
  running: '#0969da',
  queued: '#0969da',
  ready: '#6b7280',
  waiting_approval: '#9a6700',
  canceled: '#6b7280',
  skipped: '#6b7280',
  draft: '#6b7280',
}

export default function WorkflowDAG({
  run,
  onPause,
  onResume,
  onRetryStep,
}: {
  run: WorkflowRun
  onPause?: () => void
  onResume?: () => void
  onRetryStep?: (stepId: string) => void
}) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const steps = run.steps || []
  const positions = useMemo(() => layoutDag(steps), [steps])
  const byKey = useMemo(() => new Map(steps.map((s) => [s.node_key, s])), [steps])
  const selected = selectedKey ? byKey.get(selectedKey) : null

  const edges = useMemo(() => {
    const list: Array<{ from: string; to: string }> = []
    for (const s of steps) {
      for (const dep of s.dependencies || []) {
        if (byKey.has(dep)) list.push({ from: dep, to: s.node_key })
      }
    }
    return list
  }, [steps, byKey])

  const maxLayer = Math.max(0, ...Array.from(positions.values()).map((p) => p.x))
  const maxY = Math.max(0, ...Array.from(positions.values()).map((p) => p.y))
  const canvasW = maxLayer + NODE_W + 20
  const canvasH = maxY + NODE_H + 20

  const paused = run.status === 'paused'

  return (
    <div>
      <div className="dag-toolbar">
        <span className="studio__muted">
          run {run.id.slice(0, 8)} · 状态 {run.status || '-'}
        </span>
        {paused
          ? onResume && (
              <button className="studio__btn studio__btn--sm" onClick={onResume}>
                继续 run
              </button>
            )
          : onPause && (
              <button className="studio__btn studio__btn--sm" onClick={onPause}>
                暂停 run
              </button>
            )}
      </div>
      <div className="dag-wrap">
        <div className="dag-canvas">
          <div style={{ position: 'relative', width: canvasW, height: canvasH }}>
            <svg width={canvasW} height={canvasH} style={{ position: 'absolute', inset: 0 }}>
              {edges.map((e, i) => {
                const a = positions.get(e.from)!
                const b = positions.get(e.to)!
                const x1 = a.x + NODE_W
                const y1 = a.y + NODE_H / 2
                const x2 = b.x
                const y2 = b.y + NODE_H / 2
                const midX = (x1 + x2) / 2
                return (
                  <path
                    key={i}
                    d={`M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`}
                    fill="none"
                    stroke="var(--border-color, #d0d7de)"
                    strokeWidth={1.5}
                  />
                )
              })}
            </svg>
            {steps.map((s) => {
              const p = positions.get(s.node_key)!
              const color = STATUS_COLORS[s.status] || '#6b7280'
              return (
                <div
                  key={s.node_key}
                  className={`dag-node${selectedKey === s.node_key ? ' dag-node--selected' : ''}`}
                  style={{ left: p.x, top: p.y, borderLeft: `4px solid ${color}` }}
                  onClick={() => setSelectedKey(s.node_key === selectedKey ? null : s.node_key)}
                >
                  <div className="dag-node__key">{s.node_key}</div>
                  <div className="studio__muted">{s.node_type}</div>
                  <span className={`studio__badge studio__badge--${s.status}`}>{s.status}</span>
                </div>
              )
            })}
          </div>
        </div>

        {selected && (
          <aside className="dag-drawer">
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
              <b>{selected.node_key}</b>
              <button className="studio__btn studio__btn--sm" onClick={() => setSelectedKey(null)}>
                关闭
              </button>
            </div>
            <div className="dag-drawer__row">
              <span>node_type</span>
              <span>{selected.node_type}</span>
            </div>
            <div className="dag-drawer__row">
              <span>entity</span>
              <span>
                {selected.entity_type || '-'}
                {selected.entity_id ? ` / ${String(selected.entity_id).slice(0, 8)}` : ''}
              </span>
            </div>
            <div className="dag-drawer__row">
              <span>status</span>
              <span>{selected.status}</span>
            </div>
            <div className="dag-drawer__row">
              <span>attempt</span>
              <span>{selected.attempt ?? '-'}</span>
            </div>
            <div className="dag-drawer__row">
              <span>error</span>
              <span>{selected.error_code || '-'}</span>
            </div>
            {selected.input_snapshot && (
              <>
                <div className="studio__muted" style={{ margin: '8px 0 4px' }}>
                  input_snapshot
                </div>
                <pre>{JSON.stringify(selected.input_snapshot, null, 2).slice(0, 2000)}</pre>
              </>
            )}
            {selected.status === 'failed' && onRetryStep && (
              <button className="studio__btn studio__btn--sm" style={{ marginTop: 8 }} onClick={() => onRetryStep(selected.id)}>
                重试该节点
              </button>
            )}
          </aside>
        )}
      </div>
    </div>
  )
}
