import { apiFetch } from '../api'
import { ConflictError } from './nodeCanvas'

export interface FinalSettings {
  burn_subtitles: boolean
  export_srt: boolean
  bgm_artifact_id: string | null
  /** -40..0 */
  bgm_volume_db: number
  ducking: boolean
  fades: boolean
  resolution: '720p' | '1080p'
}

export interface FinalIssue {
  code: string
  message: string
  shot_index?: number
  shot_id?: string
}

export interface TimelineShot {
  index: number
  shot_id: string
  title: string
  start_ms: number
  end_ms: number
  has_video: boolean
  /** 没有视频、用首帧静帧代替 */
  still: boolean
  thumbnail_url: string | null
}

export interface TimelineVoice {
  node_id: string
  shot_index: number
  speaker: string
  kind: 'dialogue' | 'narration'
  text: string
  start_ms: number
  end_ms: number
}

export interface TimelineSubtitle { start_ms: number; end_ms: number; text: string }
export interface TimelineBgm { artifact_id: string; name: string; start_ms: number; end_ms: number }

export interface FinalTimeline {
  shots: TimelineShot[]
  voices: TimelineVoice[]
  subtitles: TimelineSubtitle[]
  bgm: TimelineBgm | null
}

/** 出片检查的一条问题：blocking 阻止导出，warning 提醒；带 start_ms / end_ms 的可以在播放器里跳转 */
export interface QcIssue {
  code: string
  level: 'blocking' | 'warning'
  message: string
  start_ms?: number
  end_ms?: number
}

/** 出片检查结果（渲染成功后由后端写入）。error = 检查本身没跑完，按提醒处理 */
export interface RenderQc {
  version: number
  status: 'passed' | 'warning' | 'failed' | 'error'
  error?: string
  issues: QcIssue[]
  notes: string[]
  checks: Record<string, { status: string } & Record<string, unknown>>
}

export interface RenderInfo {
  job_id: string
  /** queued | running | succeeded | failed | canceled */
  status: string
  progress: number
  error: string
  artifact_id: string | null
  url: string | null
  srt_url: string | null
  input_hash: string | null
  created_at: string | null
  /** 出片检查；null / 缺省 = 本功能上线前的渲染，没有检查结果 */
  qc?: RenderQc | null
}

export interface FinalState {
  episode_node_id: string | null
  settings: FinalSettings
  timeline: FinalTimeline
  total_ms: number
  problems: FinalIssue[]
  warnings: FinalIssue[]
  input_hash: string
  latest_render: RenderInfo | null
  /** 最近一次成功的渲染：最新一次在跑或失败时，页面仍用它预览和导出 */
  last_success: RenderInfo | null
  stale: boolean
}

export interface BgmUpload { artifact_id: string; name: string; duration_ms: number }

/** 有阻止渲染的问题（422）：带结构化 problems */
export class RenderBlockedError extends Error {
  problems: FinalIssue[]
  constructor(message: string, problems: FinalIssue[]) {
    super(message)
    this.problems = problems
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const message = body?.error?.message || (typeof body?.detail === 'string' ? body.detail : '') || '请求失败'
    if (response.status === 409) throw new ConflictError(message)
    if (response.status === 422 && Array.isArray(body?.problems)) throw new RenderBlockedError(message, body.problems)
    throw new Error(message)
  }
  return response.json()
}

const send = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
const base = (id: string) => `/api/node-canvases/${encodeURIComponent(id)}`

export const finalApi = {
  get: (id: string) => request<FinalState>(`${base(id)}/final`),
  ensureNode: (id: string, baseRevision: number) => request<{ episode_node_id: string; revision: number }>(`${base(id)}/final/ensure-node`, send({ base_revision: baseRevision })),
  render: (id: string, body: { base_revision: number; client_request_id: string }) => request<{ job_id: string; status: string }>(`${base(id)}/final/render`, send(body)),
  uploadBgm: (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<BgmUpload>(`${base(id)}/bgm`, { method: 'POST', body: form })
  },
  /** 受保护的产物下载：带 token 取回后触发浏览器保存 */
  download: async (url: string, filename: string) => {
    const response = await apiFetch(url)
    if (!response.ok) throw new Error('下载失败')
    const href = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = href
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(href), 1000)
  },
}
