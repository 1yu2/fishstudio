import { apiFetch } from '../api'

export type CanvasNodeType = 'text' | 'image' | 'video'
export type VideoTier = 'preview' | 'final'

export interface CanvasSummary {
  id: string
  title: string
  aspect_ratio: string
  node_count: number
  updated_at: string | null
  created_at: string | null
}

export interface StoredNode {
  id: string
  type: CanvasNodeType
  position: { x: number; y: number }
  data: Record<string, unknown>
  width?: number
  height?: number
}

export interface StoredEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string | null
  targetHandle?: string | null
}

export interface StoredGraph {
  nodes?: StoredNode[]
  edges?: StoredEdge[]
  viewport?: { x: number; y: number; zoom: number }
}

export interface CanvasDetail extends CanvasSummary {
  graph: StoredGraph
  revision: number
}

export interface NodeJob {
  id: string
  status: string
  progress: number
  /** 排队时前面还有几个任务；0 表示未知或不在排队 */
  queue_position: number
  error: string
  model: string
  created_at: string | null
}

export interface NodeResult {
  artifact_id: string
  type: string
  url: string
  tier?: string | null
  created_at?: string | null
  uploaded?: boolean
}

export interface DailyBudget {
  /** 今日云端花费上限；null 表示不限 */
  cap: number | null
  spent: number
  in_flight: number
  remaining: number | null
}

export interface CanvasRuns {
  nodes: Record<string, { jobs: NodeJob[]; results: NodeResult[] }>
  active_jobs: number
  spent: number
  budget?: DailyBudget
}

export interface CanvasModel {
  slug: string
  name: string
  model: string
  provider: string
  modes: string[]
  pricing: Record<string, unknown>
}

export interface CanvasModels {
  image: CanvasModel[]
  video: CanvasModel[]
  defaults: { image: string; video_preview: string; video_final: string }
  /** 用默认模型生成图片会遇到的问题；空字符串表示可以生成 */
  image_problem?: string
}

export interface RunRequest {
  kind: 'image' | 'video'
  prompt: string
  model?: string
  aspect_ratio?: string
  duration?: number
  tier?: VideoTier
  count?: number
  reference_artifact_ids?: string[]
  first_frame_artifact_id?: string | null
  last_frame_artifact_id?: string | null
  /** 幂等标识：每次点击生成一个，自动重试复用，后端据此去重 */
  client_request_id?: string
}

export interface Quote {
  kind: 'image' | 'video'
  model: string
  mode?: string
  resolution?: string
  estimated_cost: number | null
}

export type BatchScope = 'all' | 'downstream'

export type BatchKind = 'image' | 'video'

export interface BatchScopeBody {
  scope: BatchScope
  node_id?: string
  skip_succeeded?: boolean
  /** 只运行这些类型的节点（分段生成）；不传 = 全部 */
  kinds?: BatchKind[]
}

export interface BatchQuoteNode {
  node_id: string
  label: string
  kind: 'image' | 'video'
  status: 'pending' | 'skipped'
  estimated_cost: number | null
  unavailable: boolean
  reason: string
}

export interface BatchQuote {
  /** 报价时的画布版本；提交批量运行时原样带回，服务端据此拒绝过期报价 */
  revision: number
  nodes: BatchQuoteNode[]
  estimated_total: number
  /** 计入每日预算的云端部分 */
  billable_total: number
  /** 真正会提交任务的节点数（旧版服务端没有以下三个字段） */
  runnable_count?: number
  /** 分段运行时缺上游结果、会先跳过的节点数 */
  waiting_count?: number
  /** 要先处理、否则整个批次无法运行的节点数 */
  blocking_count?: number
  /** runs=true 只是价格未知、照常运行；blocks_batch=true 要先处理才能运行 */
  unavailable_nodes: { node_id: string; label: string; reason: string; runs?: boolean; blocks_batch?: boolean }[]
  budget: { cap: number | null; remaining: number | null }
  exceeds_budget: boolean
}

export interface BatchNodeState {
  status: 'waiting' | 'running' | 'succeeded' | 'failed' | 'blocked' | 'canceled' | 'skipped'
  job_id: string | null
  reason: string
}

export interface BatchRun {
  id: string
  status: 'running' | 'succeeded' | 'failed' | 'canceled'
  /** 批次限定的节点类型；null / 缺省 = 全部 */
  kinds?: BatchKind[] | null
  node_ids: string[]
  nodes: Record<string, BatchNodeState>
  total: number
  /** 已成功或被跳过的节点数 */
  completed: number
  /** 失败或被上游阻塞的节点数 */
  failed: number
  estimated_total: number
}

export interface SplitResult {
  characters: { name: string; description: string }[]
  shots: { title: string; prompt: string; characters: string[]; duration_s: number }[]
}

export class ConflictError extends Error {}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const message = body?.error?.message || body?.detail?.message || (typeof body?.detail === 'string' ? body.detail : '') || '请求失败'
    if (response.status === 409) throw new ConflictError(message)
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

const send = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

const base = (id: string) => `/api/node-canvases/${encodeURIComponent(id)}`

export const nodeCanvasApi = {
  list: () => json<CanvasSummary[]>('/api/node-canvases'),
  create: (body: { title?: string; aspect_ratio?: string; idea?: string }) => json<CanvasDetail>('/api/node-canvases', send('POST', body)),
  get: (id: string) => json<CanvasDetail>(base(id)),
  rename: (id: string, body: { title?: string; aspect_ratio?: string }) => json<CanvasSummary>(base(id), send('PATCH', body)),
  remove: (id: string) => json<void>(base(id), { method: 'DELETE' }),
  save: (id: string, graph: StoredGraph, revision: number) => json<{ revision: number }>(`${base(id)}/graph`, send('PUT', { graph, revision })),
  runs: (id: string) => json<CanvasRuns>(`${base(id)}/runs`),
  models: () => json<CanvasModels>('/api/node-canvases/models'),
  quote: (id: string, body: RunRequest) => json<Quote>(`${base(id)}/quote`, send('POST', body)),
  run: (id: string, nodeId: string, body: RunRequest) =>
    json<{ job_id: string; status: string }>(`${base(id)}/nodes/${encodeURIComponent(nodeId)}/run`, send('POST', body)),
  batchQuote: (id: string, body: BatchScopeBody) => json<BatchQuote>(`${base(id)}/batch-quote`, send('POST', body)),
  batchRun: (id: string, body: BatchScopeBody & { client_request_id: string; quoted_revision?: number }) => json<BatchRun>(`${base(id)}/batch-runs`, send('POST', body)),
  batchList: (id: string) => json<BatchRun[]>(`${base(id)}/batch-runs`),
  batchGet: (id: string, batchId: string, signal?: AbortSignal) => json<BatchRun>(`${base(id)}/batch-runs/${encodeURIComponent(batchId)}`, { signal }),
  batchCancel: (id: string, batchId: string) => json<BatchRun>(`${base(id)}/batch-runs/${encodeURIComponent(batchId)}/cancel`, { method: 'POST' }),
  upload: (id: string, nodeId: string, file: File) => {
    const form = new FormData()
    form.append('node_id', nodeId)
    form.append('file', file)
    return json<NodeResult>(`${base(id)}/uploads`, { method: 'POST', body: form })
  },
  cancelJob: (jobId: string) => json<unknown>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }),
  split: (id: string, idea: string, shots: number) => json<SplitResult>(`${base(id)}/copilot/split`, send('POST', { idea, shots })),
}
