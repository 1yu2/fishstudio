/**
 * 短剧/系列视频领域 API client（/api/series、/api/workflow、/api/jobs、/api/usage）。
 * 全部走 apiFetch（自动带 Bearer token，401 统一处理）。
 */
import { apiFetch } from '../api'

// ---------- 类型 ----------

export type ProjectStatus = 'draft' | 'in_progress' | 'completed' | 'archived' | string
export type AssetType = 'character' | 'style' | 'scene' | 'voice'
export type AssetStatus = 'draft' | 'preview_ready' | 'approved' | 'retired' | string
export type StepStatus =
  | 'draft'
  | 'ready'
  | 'waiting_approval'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled'
  | 'skipped'
  | string
export type JobStatus = 'pending' | 'leased' | 'running' | 'succeeded' | 'failed' | 'canceled' | string

export interface SeriesProject {
  id: string
  title: string
  project_type: string
  status: ProjectStatus
  settings?: Record<string, unknown>
  canvas_id?: string | null
  created_at?: string
  updated_at?: string
  episodes?: SeriesEpisode[]
}

export interface SeriesEpisode {
  id: string
  project_id: string
  index: number
  title?: string
  status?: string
  storyboard_revision?: number
}

export interface ShotTrack {
  id?: string
  text?: string
  role?: string
  [key: string]: unknown
}

export interface StoryboardShot {
  id: string
  index: number
  selected_image_artifact_id?: string | null
  selected_video_artifact_id?: string | null
  duration_ms?: number
  visual_prompt?: string
  camera?: string
  transition?: string
  aspect_ratio?: string
  status?: string
  dialogue_tracks?: ShotTrack[]
  narration_tracks?: ShotTrack[]
  subtitle_tracks?: ShotTrack[]
  [key: string]: unknown
}

export interface StoryboardScene {
  id: string
  index: number
  title?: string
  description?: string
  shots: StoryboardShot[]
  [key: string]: unknown
}

export interface StoryboardEpisodeNode {
  id: string
  index: number
  title?: string
  revision?: number
  scenes: StoryboardScene[]
  [key: string]: unknown
}

export interface StoryboardData {
  episodes: StoryboardEpisodeNode[]
  [key: string]: unknown
}

export interface SeriesAsset {
  id: string
  project_id?: string | null // scope=personal 的个人资产为 null（P2-3）
  scope?: 'project' | 'personal' | string
  provider_meta?: Record<string, unknown>
  type: AssetType
  name: string
  description?: string
  tags?: string[]
  status: AssetStatus
  version?: number
  current_version?: number
  versions?: AssetVersion[]
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export interface AssetVersion {
  id: string
  asset_id: string
  version: number
  status?: string
  [key: string]: unknown
}

export interface ApprovalRecord {
  id: string
  project_id?: string
  target_type: 'storyboard' | 'asset' | string
  target_id: string
  target_revision?: number
  action?: 'approve' | 'reject' | null
  comment?: string
  status?: string
  created_at?: string
  [key: string]: unknown
}

export interface GenerationGate {
  ok: boolean
  reasons: string[]
}

export interface WorkflowStep {
  id: string
  node_key: string
  node_type: string
  entity_type?: string
  entity_id?: string
  status: StepStatus
  attempt?: number
  error_code?: string | null
  selected_artifact_id?: string | null
  input_snapshot?: Record<string, unknown>
  dependencies?: string[]
  [key: string]: unknown
}

export interface WorkflowRun {
  id: string
  project_id: string
  goal?: string
  status?: string
  steps: WorkflowStep[]
  estimated_cost?: number
  cost_limit?: number
  /** LLM Planner 实际使用的模型（use_llm_planner 创建时；rule-based 表示规则回退） */
  planner_model?: string
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export interface Job {
  id: string
  project_id?: string
  shot_id?: string | null
  operation?: string
  provider?: string
  model?: string
  status: JobStatus
  progress?: number
  attempt?: number
  error?: string | null
  params?: Record<string, unknown>
  result?: { storage_key?: string; url?: string; [key: string]: unknown } | null
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export interface UsageRow {
  provider?: string
  total_cost?: number
  job_count?: number
  [key: string]: unknown
}

// ---------- 费用报价（P2-6） ----------

export interface CostQuoteItem {
  step_node_key: string
  operation: string
  provider?: string
  model?: string
  resolution?: string
  candidate_count?: number
  unit_count: number
  unit_price: string // Decimal 序列化为字符串
  subtotal: string
}

export interface CostQuote {
  quote_id: string
  run_id: string
  items: CostQuoteItem[]
  total: string
  price_table_version: string
  created_at?: string
  /** 复审 0.6：报价与估算输入绑定，报价后分镜/参数变化 → input_hash 不一致 → 失效 */
  input_hash?: string
  storyboard_revision_id?: Record<string, string | null>
  /** ISO8601，创建后 30 分钟有效 */
  expires_at?: string
}

// ---------- 候选产物与反馈（P2-2） ----------

export interface ShotArtifact {
  artifact_id: string
  type?: string
  url?: string
  job_id?: string
  operation?: string
  provider?: string
  model?: string
  job_status?: string
  created_at?: string | null
  [key: string]: unknown
}

export type FeedbackAction = 'selected' | 'rejected' | 'rated'

export interface UploadedArtifact {
  artifact_id: string
  type: string
  filename: string
  size: number
  url: string
}

export interface CloneVoiceQuote {
  quote_id: string
  provider: string
  price: string
  currency: string
  price_table_version: string
  expires_at: string
  voice_asset_id: string
  voice_asset_version: number
  reference_artifact_id: string
}

export interface CloneVoiceJobResponse {
  job_id: string
  run_id?: string
  status: string
}

// ---------- 请求辅助 ----------

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init)
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    let message = detail
    try {
      // FastAPI 错误体为 {"detail": "..."}，提取中文具体原因（如 409 报价失效原因）
      const parsed = JSON.parse(detail) as { detail?: unknown }
      if (typeof parsed.detail === 'string') message = parsed.detail
      else if (parsed.detail != null) message = JSON.stringify(parsed.detail)
    } catch {
      // 非 JSON 错误体，原样展示
    }
    const err = new Error(message || `请求失败 (${res.status})`) as Error & { status?: number }
    err.status = res.status
    throw err
  }
  return (await res.json()) as T
}

const json = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// ---------- series ----------

export function createProject(input: {
  canvas_id?: string
  title: string
  project_type?: string
  settings?: Record<string, unknown>
}): Promise<SeriesProject> {
  return request('/api/series/projects', {
    method: 'POST',
    ...json({ project_type: 'short_drama', ...input }),
  })
}

export function listProjects(params: { project_type?: string; status?: string } = {}): Promise<SeriesProject[]> {
  const q = new URLSearchParams()
  if (params.project_type) q.set('project_type', params.project_type)
  if (params.status) q.set('status', params.status)
  const suffix = q.toString() ? `?${q}` : ''
  return request(`/api/series/projects${suffix}`)
}

export function getProject(projectId: string): Promise<SeriesProject> {
  return request(`/api/series/projects/${projectId}`)
}

export function getStoryboard(projectId: string, episodeId?: string): Promise<StoryboardData> {
  const q = episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : ''
  return request(`/api/series/projects/${projectId}/storyboard${q}`)
}

export function saveStoryboard(episodeId: string, payload: Record<string, unknown>): Promise<unknown> {
  return request(`/api/series/episodes/${episodeId}/storyboard`, { method: 'PUT', ...json({ payload }) })
}

export function importStoryboardText(episodeId: string, text: string): Promise<unknown> {
  return request(`/api/series/episodes/${episodeId}/storyboard/import-text`, { method: 'POST', ...json({ text }) })
}

export type ShotPatch = Partial<
  Pick<
    StoryboardShot,
    | 'duration_ms'
    | 'visual_prompt'
    | 'camera'
    | 'transition'
    | 'aspect_ratio'
    | 'dialogue_tracks'
    | 'narration_tracks'
    | 'subtitle_tracks'
    | 'status'
  >
>

export function patchShot(shotId: string, patch: ShotPatch): Promise<StoryboardShot> {
  return request(`/api/series/shots/${shotId}`, { method: 'PATCH', ...json(patch) })
}

export function createAsset(
  projectId: string,
  input: { type: AssetType; name: string; description?: string; tags?: string[] } & Record<string, unknown>,
): Promise<SeriesAsset> {
  return request(`/api/series/projects/${projectId}/assets`, { method: 'POST', ...json(input) })
}

export function listAssets(
  projectId: string,
  params: { type?: AssetType; tag?: string; q?: string } = {},
): Promise<SeriesAsset[]> {
  const q = new URLSearchParams()
  if (params.type) q.set('type', params.type)
  if (params.tag) q.set('tag', params.tag)
  if (params.q) q.set('q', params.q)
  const suffix = q.toString() ? `?${q}` : ''
  return request(`/api/series/projects/${projectId}/assets${suffix}`)
}

export function createAssetVersion(assetId: string, body: Record<string, unknown> = {}): Promise<SeriesAsset> {
  return request(`/api/series/assets/${assetId}/versions`, { method: 'POST', ...json(body) })
}

export function uploadProjectAudio(projectId: string, file: File): Promise<UploadedArtifact> {
  const form = new FormData()
  form.append('file', file)
  return request(`/api/series/projects/${projectId}/artifacts/upload-audio`, {
    method: 'POST',
    body: form,
  })
}

export function getCloneVoiceQuote(
  projectId: string,
  assetId: string,
  referenceAudioArtifactId: string,
): Promise<CloneVoiceQuote> {
  const query = new URLSearchParams({ reference_audio_artifact_id: referenceAudioArtifactId })
  return request(`/api/series/projects/${projectId}/assets/${assetId}/clone-voice-quote?${query}`)
}

export function confirmCloneVoice(
  projectId: string,
  assetId: string,
  input: { reference_audio_artifact_id: string; confirmed_quote_id: string; voice_name?: string },
): Promise<CloneVoiceJobResponse> {
  return request(`/api/series/projects/${projectId}/assets/${assetId}/clone-voice`, {
    method: 'POST',
    ...json(input),
  })
}

export function bindAssetToShot(assetId: string, shotId: string, role: string): Promise<unknown> {
  return request(`/api/series/assets/${assetId}/bind-shot`, { method: 'POST', ...json({ shot_id: shotId, role }) })
}

export function submitApproval(input: {
  project_id: string
  target_type: 'storyboard' | 'asset'
  target_id: string
  target_revision: number
  action: 'approve' | 'reject'
  comment?: string
}): Promise<unknown> {
  return request('/api/series/approvals', { method: 'POST', ...json(input) })
}

export function listApprovals(projectId: string): Promise<ApprovalRecord[]> {
  return request(`/api/series/projects/${projectId}/approvals`)
}

export function getGenerationGate(episodeId: string): Promise<GenerationGate> {
  return request(`/api/series/episodes/${episodeId}/generation-gate`)
}

// ---------- workflow ----------

export function createRun(projectId: string, goal?: string, useLlmPlanner = false): Promise<WorkflowRun> {
  return request('/api/workflow/runs', {
    method: 'POST',
    ...json({ project_id: projectId, goal, use_llm_planner: useLlmPlanner }),
  })
}

export function listRuns(projectId: string): Promise<WorkflowRun[]> {
  return request(`/api/workflow/runs?project_id=${encodeURIComponent(projectId)}`)
}

export function getRun(runId: string): Promise<WorkflowRun> {
  // 后端返回 { run, steps, dependencies } 结构，这里拍平成 WorkflowRun 方便前端使用
  return request<Record<string, unknown>>(`/api/workflow/runs/${runId}`).then((data) => {
    const inner = (data.run as Record<string, unknown> | undefined) ?? {}
    return { ...inner, steps: (data.steps as WorkflowRun['steps']) ?? [], dependencies: data.dependencies } as unknown as WorkflowRun
  })
}

export function getCostQuote(runId: string): Promise<CostQuote> {
  return request(`/api/workflow/runs/${runId}/cost-quote`)
}

/**
 * 确认费用（复审 0.6）：必须绑定 GET cost-quote 返回的 quote_id。
 * 报价过期/不匹配/价格表或分镜已变化时后端返回 409，detail 为中文具体原因
 * （err.status === 409，err.message 含 detail）。
 */
export function confirmCost(runId: string, quoteId: string): Promise<unknown> {
  return request(`/api/workflow/runs/${runId}/confirm-cost`, { method: 'POST', ...json({ quote_id: quoteId }) })
}

export function pauseRun(runId: string): Promise<unknown> {
  return request(`/api/workflow/runs/${runId}/pause`, { method: 'POST' })
}

export function resumeRun(runId: string): Promise<unknown> {
  return request(`/api/workflow/runs/${runId}/resume`, { method: 'POST' })
}

export function retryStep(stepId: string): Promise<unknown> {
  return request(`/api/workflow/steps/${stepId}/retry`, { method: 'POST' })
}

export function completeStep(stepId: string): Promise<unknown> {
  return request(`/api/workflow/steps/${stepId}/complete`, { method: 'POST' })
}

// ---------- jobs & usage ----------

export function listJobs(params: { project_id?: string; status?: string } = {}): Promise<Job[]> {
  const q = new URLSearchParams()
  if (params.project_id) q.set('project_id', params.project_id)
  if (params.status) q.set('status', params.status)
  const suffix = q.toString() ? `?${q}` : ''
  return request(`/api/jobs${suffix}`)
}

export function retryJob(jobId: string): Promise<unknown> {
  return request(`/api/jobs/${jobId}/retry`, { method: 'POST' })
}

export function cancelJob(jobId: string): Promise<unknown> {
  return request(`/api/jobs/${jobId}/cancel`, { method: 'POST' })
}

export function getUsageSummary(projectId?: string, groupBy = 'provider'): Promise<UsageRow[]> {
  const q = new URLSearchParams({ group_by: groupBy })
  if (projectId) q.set('project_id', projectId)
  return request(`/api/usage/summary?${q}`)
}

// ---------- 候选产物与反馈 ----------

export function listShotArtifacts(shotId: string, operation?: string): Promise<ShotArtifact[]> {
  const q = operation ? `?operation=${encodeURIComponent(operation)}` : ''
  return request(`/api/series/shots/${shotId}/artifacts${q}`)
}

export function submitArtifactFeedback(
  artifactId: string,
  input: { action: FeedbackAction; score?: number; reason?: string; step_id?: string },
): Promise<unknown> {
  return request(`/api/series/artifacts/${artifactId}/feedback`, { method: 'POST', ...json(input) })
}

// ---------- 个人资产库（P2-3，/api/assets/personal） ----------

export function createPersonalAsset(input: {
  type: AssetType
  name: string
  description?: string
  tags?: string[]
  provider_meta?: Record<string, unknown>
}): Promise<SeriesAsset> {
  return request('/api/assets/personal', { method: 'POST', ...json(input) })
}

export function listPersonalAssets(params: { type?: AssetType; tag?: string; q?: string } = {}): Promise<SeriesAsset[]> {
  const q = new URLSearchParams()
  if (params.type) q.set('type', params.type)
  if (params.tag) q.set('tag', params.tag)
  if (params.q) q.set('q', params.q)
  const suffix = q.toString() ? `?${q}` : ''
  return request(`/api/assets/personal${suffix}`)
}

export function createPersonalAssetVersion(assetId: string, updates: Record<string, unknown> = {}): Promise<SeriesAsset> {
  return request(`/api/assets/personal/${assetId}/versions`, { method: 'POST', ...json({ updates }) })
}

export function copyPersonalAssetToProject(assetId: string, projectId: string): Promise<SeriesAsset> {
  return request(`/api/assets/personal/${assetId}/copy-to-project`, { method: 'POST', ...json({ project_id: projectId }) })
}
