/**
 * 短剧/系列视频领域 API client（/api/series、/api/workflow、/api/jobs、/api/usage）。
 * 全部走 apiFetch（自动带 Bearer token，401 统一处理）。
 */
import { apiFetch } from '../api'

// ---------- 类型 ----------

export type ProjectStatus = 'draft' | 'in_progress' | 'completed' | 'archived' | string
export type AssetType = 'character' | 'style' | 'scene' | 'voice' | 'prop'
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

export interface WorkspaceSummary {
  project_id: string
  workspace_mode: 'quick' | 'professional'
  stage: string
  progress: { completed: number; total: number }
  budget: { limit_cny?: number | null; spent_cny: number }
  stale_impact_count: number
  qa_failed_count: number
  pending_approval_count: number
  job_error_count: number
  run_id?: string | null
}

export interface ReferenceSegment {
  key: string
  start_ms: number
  end_ms: number
  shot_size?: string
  camera_movement?: string
  composition?: string
  action_intervals?: Array<Record<string, unknown>>
  emotion_curve?: Array<Record<string, unknown>>
  keyframes_ms?: number[]
  confidence?: number
  [key: string]: unknown
}

export interface ReferenceAnalysis {
  id: string
  project_id: string
  artifact_id: string
  revision: number
  status: string
  duration_ms: number
  segments: ReferenceSegment[]
  confidence: number
  model_snapshot: Record<string, unknown>
  usage_snapshot: Record<string, unknown>
  estimated_cost: string
  source_uri: string
  authorization: string
  usage_scope: string
}

export interface SeriesEpisode {
  id: string
  project_id: string
  index: number
  title?: string
  status?: string
  storyboard_revision?: number
  objective?: string
  hook?: string
  conflict?: string
  payoff?: string
  cliffhanger?: string
  target_duration_ms?: number
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
  shot_type?: string
  narrative_purpose?: string
  composition?: string
  action_start?: string
  action_end?: string
  emotion?: string
  first_frame_prompt?: string
  last_frame_prompt?: string
  generation_mode?: string
  continuity_constraints?: Record<string, unknown>
  risk_tags?: string[]
  locked_asset_versions?: Record<string, number>
  qa_status?: string
  camera?: Record<string, unknown>
  transition?: Record<string, unknown>
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
  revision_status?: string
  scenes: StoryboardScene[]
  [key: string]: unknown
}

export interface StoryboardData {
  episodes: StoryboardEpisodeNode[]
  [key: string]: unknown
}

export interface TimelineRevision {
  id: string
  project_id: string
  episode_id: string
  revision: number
  parent_revision_id?: string | null
  status: string
  content_hash: string
  spec: Record<string, unknown>
  approved_at?: string | null
}

export interface QCReport {
  id: string
  episode_id: string
  phase: 'director_review' | 'final_qc' | string
  status: 'passed' | 'failed' | string
  score: number
  render_artifact_id: string
  issues: Array<{ code: string; severity: string; detail?: string; target?: Record<string, unknown> }>
  repair_targets: Array<{ issue_code: string; return_to: string; target?: Record<string, unknown> }>
}

export interface Publication {
  id: string
  episode_id: string
  platform: string
  status: string
  title: string
  description: string
  tags: string[]
  render_artifact_id: string
  cover_artifact_id?: string | null
  manifest_artifact_id?: string | null
}

export interface ScriptRevision {
  id: string
  episode_id: string
  revision: number
  schema_version: number
  structured_payload: {
    episode_objective: string
    hook: string
    conflict: string
    payoff?: string
    cliffhanger: string
    scenes: Array<Record<string, unknown>>
    character_state_changes?: Record<string, unknown>
    carry_over?: string[]
    [key: string]: unknown
  }
  source_type: 'agent' | 'user' | 'import' | string
  based_on_revision?: number | null
  status: string
  created_by?: string
  created_at?: string
}

export interface DirectorPlanRevision {
  id: string
  episode_id: string
  revision: number
  based_on_script_revision_id: string
  payload: Record<string, unknown>
  platform_constraints: Record<string, unknown>
  asset_version_snapshot: Record<string, unknown>
  status: string
  created_at: string
}

export interface ObserverFinding {
  code: string
  category: string
  severity: 'info' | 'warning' | 'major' | 'critical'
  evidence: string
  location: Record<string, unknown>
  suggested_fix: string
}

export interface ObserverReview {
  id: string
  review_type: string
  target_type: string
  target_id: string
  target_revision: number
  score: number
  blocking: boolean
  deterministic_findings: ObserverFinding[]
  specialist_findings: ObserverFinding[]
  llm_findings: ObserverFinding[]
  summary: string
  created_at: string
  review_scope?: { issue_codes?: string[]; categories?: string[] }
  model_cost_cny?: number | null
}

export type BibleKind = 'series' | 'style'

export interface BibleRevision {
  id: string
  project_id: string
  revision: number
  schema_version: number
  structured_payload: Record<string, unknown>
  source_type: 'agent' | 'user' | 'import' | string
  based_on_revision?: number | null
  status: string
  created_by?: string
  created_at?: string
}

export interface SeriesAsset {
  id: string
  project_id?: string | null // scope=personal 的个人资产为 null（P2-3）
  scope?: 'project' | 'personal' | string
  provider_meta?: Record<string, unknown>
  profile_schema_version?: number
  profile?: Record<string, unknown>
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
  target_type: 'script' | 'storyboard' | 'asset' | string
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

export interface InvalidationPreview {
  project_id: string
  source_type: string
  source_id: string
  changed_fields: string[]
  stages: string[]
  affected: Array<{
    entity_type: string
    entity_id: string
    stage: string
    action: string
    reason: string
  }>
  affected_count: number
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

export interface JobEvent {
  sequence: number
  event: string
  project_id: string
  run_id?: string
  step_id?: string
  job_id: string
  entity_type?: string
  entity_id?: string
  status: string
  progress: number
  payload: Record<string, unknown>
  occurred_at?: string
}

export interface EvalRoutingStat {
  deployment: string
  provider: string
  model: string
  quality_score: number
  p95_latency_ms?: number | null
  unit_quality_cost_cny: string
  health_score: number
  sample_runs: number
  main_failure_reasons: Array<[string, number]>
}

export interface UsageRow {
  key?: string
  provider?: string
  operation?: string
  total_cost?: number
  job_count?: number
  estimated_cost?: number
  actual_cost?: number
  input_units?: number
  output_units?: number
  unpriced_count?: number
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
  duration_ms?: number | null
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

export function startStudioProject(input: {
  title: string
  mode: 'quick' | 'professional'
  story?: string
  style?: string
  target_duration_ms?: number
  episode_count?: number
  budget_cny?: number
  aspect_ratio?: string
}): Promise<{ project: SeriesProject; run: { id: string; status: string } }> {
  return request('/api/series/studio/start', { method: 'POST', ...json(input) })
}

export function setWorkspaceMode(projectId: string, mode: 'quick' | 'professional'): Promise<SeriesProject> {
  return request(`/api/series/projects/${projectId}/workspace-mode`, { method: 'PATCH', ...json({ mode }) })
}

export function getWorkspaceSummary(projectId: string): Promise<WorkspaceSummary> {
  return request(`/api/series/projects/${projectId}/workspace-summary`)
}

export function listProjects(params: { project_type?: string; status?: string } = {}): Promise<SeriesProject[]> {
  const q = new URLSearchParams()
  if (params.project_type) q.set('project_type', params.project_type)
  if (params.status) q.set('status', params.status)
  const suffix = q.toString() ? `?${q}` : ''
  return request(`/api/series/projects${suffix}`)
}

export function getProject(projectId: string): Promise<SeriesProject> {
  return request<Omit<SeriesProject, 'episodes'> & { episodes?: Array<Record<string, unknown>> }>(`/api/series/projects/${projectId}`).then(
    (project) => ({
      ...project,
      episodes: (project.episodes || []).map((episode) => ({
        ...episode,
        index: Number(episode.episode_no ?? episode.index ?? 0),
        storyboard_revision: Number(episode.current_revision ?? episode.storyboard_revision ?? 0) || undefined,
      })) as SeriesEpisode[],
    }),
  )
}

export function getStoryboard(projectId: string, episodeId?: string): Promise<StoryboardData> {
  const q = episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : ''
  return request<Array<Record<string, unknown>>>(`/api/series/projects/${projectId}/storyboard${q}`).then((episodes) => ({
    episodes: episodes.map((episode) => ({
      ...episode,
      index: Number(episode.episode_no ?? episode.index ?? 0),
      revision: Number(episode.current_revision ?? episode.revision ?? 0) || undefined,
      scenes: ((episode.scenes as Array<Record<string, unknown>> | undefined) || []).map((scene) => ({
        ...scene,
        index: Number(scene.scene_no ?? scene.index ?? 0),
        title: String(scene.location ?? scene.title ?? ''),
        shots: ((scene.shots as Array<Record<string, unknown>> | undefined) || []).map((shot) => ({
          ...shot,
          index: Number(shot.shot_no ?? shot.index ?? 0),
        })),
      })),
    })) as StoryboardEpisodeNode[],
  }))
}

export function saveStoryboard(
  episodeId: string,
  payload: Record<string, unknown>,
  basedOnScriptRevisionId?: string,
): Promise<unknown> {
  return request(`/api/series/episodes/${episodeId}/storyboard`, {
    method: 'PUT',
    ...json({ payload, based_on_script_revision_id: basedOnScriptRevisionId }),
  })
}

export function listScriptRevisions(episodeId: string): Promise<ScriptRevision[]> {
  return request(`/api/series/episodes/${episodeId}/scripts`)
}

export function createScriptRevision(
  episodeId: string,
  input: {
    structured_payload: ScriptRevision['structured_payload']
    source_type?: 'agent' | 'user' | 'import'
    based_on_revision?: number
  },
): Promise<ScriptRevision> {
  return request(`/api/series/episodes/${episodeId}/scripts`, { method: 'POST', ...json(input) })
}

export function listBibleRevisions(projectId: string, kind: BibleKind): Promise<BibleRevision[]> {
  return request(`/api/series/projects/${projectId}/bibles/${kind}`)
}

export function createBibleRevision(
  projectId: string,
  kind: BibleKind,
  input: {
    structured_payload: Record<string, unknown>
    source_type?: 'agent' | 'user' | 'import'
    based_on_revision?: number
  },
): Promise<BibleRevision> {
  return request(`/api/series/projects/${projectId}/bibles/${kind}`, {
    method: 'POST',
    ...json(input),
  })
}

export function listDirectorPlans(episodeId: string): Promise<DirectorPlanRevision[]> {
  return request(`/api/series/episodes/${episodeId}/director-plans`)
}

export function createDirectorPlan(episodeId: string, input: {
  based_on_script_revision_id: string
  payload: Record<string, unknown>
  asset_version_snapshot?: Record<string, unknown>
  platform_constraints?: Record<string, unknown>
  source_type?: string
}): Promise<DirectorPlanRevision> {
  return request(`/api/series/episodes/${episodeId}/director-plans`, { method: 'POST', ...json(input) })
}

export function listObserverReviews(episodeId: string): Promise<ObserverReview[]> {
  return request(`/api/series/episodes/${episodeId}/observer-reviews`)
}

export function runObserverReview(episodeId: string, input: {
  review_type: string
  target_type: 'script' | 'storyboard'
  target_id: string
  target_revision: number
  issue_codes?: string[]
  categories?: string[]
}): Promise<ObserverReview> {
  return request(`/api/series/episodes/${episodeId}/observer-reviews/run`, { method: 'POST', ...json(input) })
}

export function importStoryboardText(episodeId: string, text: string): Promise<unknown> {
  return request(`/api/series/episodes/${episodeId}/storyboard/import-text`, { method: 'POST', ...json({ text }) })
}

export type ShotPatch = Partial<
  Pick<
    StoryboardShot,
    | 'duration_ms'
    | 'shot_type'
    | 'narrative_purpose'
    | 'composition'
    | 'action_start'
    | 'action_end'
    | 'emotion'
    | 'first_frame_prompt'
    | 'last_frame_prompt'
    | 'generation_mode'
    | 'continuity_constraints'
    | 'risk_tags'
    | 'locked_asset_versions'
    | 'qa_status'
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

export function uploadReferenceVideo(projectId: string, file: File): Promise<UploadedArtifact> {
  const form = new FormData()
  form.append('file', file)
  return request(`/api/series/projects/${projectId}/artifacts/upload-reference-video`, { method: 'POST', body: form })
}

export function analyzeReferenceVideo(projectId: string, input: {
  artifact_id: string; duration_ms: number; source_uri?: string; authorization: 'owned' | 'licensed' | 'public_domain'; usage_scope?: string
}): Promise<ReferenceAnalysis> {
  return request(`/api/series/projects/${projectId}/reference-analyses`, { method: 'POST', ...json(input) })
}

export function listReferenceAnalyses(projectId: string): Promise<ReferenceAnalysis[]> {
  return request(`/api/series/projects/${projectId}/reference-analyses`)
}

export function reviseReferenceAnalysis(analysisId: string, segments: ReferenceSegment[]): Promise<ReferenceAnalysis> {
  return request(`/api/series/reference-analyses/${analysisId}/revisions`, { method: 'POST', ...json({ segments }) })
}

export function bindReferenceSegment(analysisId: string, input: {
  shot_id: string; segment_key: string; reference_type: 'composition' | 'motion' | 'timing'; weight?: number; replace_subject?: boolean
}): Promise<unknown> {
  return request(`/api/series/reference-analyses/${analysisId}/bind`, { method: 'POST', ...json(input) })
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
  target_type: 'series_bible' | 'style_bible' | 'script' | 'director_plan' | 'storyboard' | 'asset'
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

export function previewInvalidation(
  projectId: string,
  input: { source_type: string; source_id: string; changed_fields: string[]; reason?: string },
): Promise<InvalidationPreview> {
  return request(`/api/series/projects/${projectId}/invalidation/preview`, {
    method: 'POST',
    ...json(input),
  })
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
  return request<{ jobs?: Job[] }>(`/api/jobs${suffix}`).then((data) => data.jobs || [])
}

export function listJobEvents(projectId: string, after = 0): Promise<JobEvent[]> {
  const query = new URLSearchParams({ project_id: projectId, after: String(after) })
  return request<{ events?: JobEvent[] }>(`/api/jobs/events?${query}`).then((data) => data.events || [])
}

export function getEvalRoutingStats(): Promise<EvalRoutingStat[]> {
  return request('/api/eval/routing-stats')
}

export function getProjectEvalFeedback(projectId: string): Promise<Record<string, unknown>> {
  return request(`/api/eval/projects/${projectId}/feedback`)
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
  return request<{ rows?: Array<Record<string, unknown>> }>(`/api/usage/summary?${q}`).then((data) =>
    (data.rows || []).map((row) => ({
      ...row,
      key: String(row[groupBy] ?? ''),
      provider: groupBy === 'provider' ? String(row.provider ?? '') : undefined,
      operation: groupBy === 'operation' ? String(row.operation ?? '') : undefined,
      job_count: Number(row.count ?? 0),
      total_cost: Number(row.actual_cost ?? row.estimated_cost ?? 0),
      unpriced_count: Number(row.unpriced_count ?? 0),
    })),
  )
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

export interface ArtifactCandidate {
  id: string
  shot_id: string
  artifact_id: string
  job_id?: string | null
  candidate_index: number
  status: 'pending' | 'evaluating' | 'approved' | 'rejected' | 'superseded' | string
  score?: number | null
  qa_summary: Record<string, unknown>
  qa_findings: Array<Record<string, unknown>>
  rejection_reason?: string
}

export function listShotCandidates(shotId: string): Promise<ArtifactCandidate[]> {
  return request(`/api/series/shots/${shotId}/candidates`)
}

export function evaluateShotCandidate(
  shotId: string,
  candidateId: string,
  input: { findings?: Array<Record<string, unknown>>; score?: number; evaluated_by?: string } = {},
): Promise<ArtifactCandidate> {
  return request(`/api/series/shots/${shotId}/candidates/${candidateId}/evaluate`, { method: 'POST', ...json(input) })
}

export function promoteShotCandidate(shotId: string, candidateId: string): Promise<ArtifactCandidate> {
  return request(`/api/series/shots/${shotId}/candidates/${candidateId}/promote`, { method: 'POST' })
}

export function rejectShotCandidate(shotId: string, candidateId: string, reason: string): Promise<ArtifactCandidate> {
  return request(`/api/series/shots/${shotId}/candidates/${candidateId}/reject`, { method: 'POST', ...json({ reason }) })
}

export function regenerateShotCandidate(
  shotId: string,
  candidateId: string,
  input: { reason?: string; instruction?: string; preserve_fields?: string[]; replace_fields?: string[]; confirmed_quote_id?: string } = {},
): Promise<{ job_id: string }> {
  return request(`/api/series/shots/${shotId}/candidates/${candidateId}/regenerate`, { method: 'POST', ...json(input) })
}

export function listTimelineRevisions(episodeId: string): Promise<TimelineRevision[]> {
  return request(`/api/series/episodes/${episodeId}/timeline-revisions`)
}

export function planTimelineRevision(episodeId: string): Promise<TimelineRevision> {
  return request(`/api/series/episodes/${episodeId}/timeline-revisions/plan`, { method: 'POST' })
}

export function createTimelineRevision(
  episodeId: string,
  input: { spec: Record<string, unknown>; parent_revision_id?: string; storyboard_revision_id?: string },
): Promise<TimelineRevision> {
  return request(`/api/series/episodes/${episodeId}/timeline-revisions`, { method: 'POST', ...json(input) })
}

export function approveTimelineRevision(episodeId: string, revisionId: string): Promise<TimelineRevision> {
  return request(`/api/series/episodes/${episodeId}/timeline-revisions/${revisionId}/approve`, { method: 'POST' })
}

export function runEpisodeQC(
  episodeId: string,
  input: { phase: 'director_review' | 'final_qc'; render_artifact_id?: string },
): Promise<QCReport> {
  return request(`/api/series/episodes/${episodeId}/qc`, { method: 'POST', ...json(input) })
}

export function listEpisodeQC(episodeId: string): Promise<QCReport[]> {
  return request(`/api/series/episodes/${episodeId}/qc`)
}

export function createPublishPackage(
  episodeId: string,
  input: { platform?: string; title?: string; description?: string; tags?: string[] },
): Promise<Publication> {
  return request(`/api/series/episodes/${episodeId}/publish-package`, { method: 'POST', ...json(input) })
}

export function listPublications(projectId: string): Promise<Publication[]> {
  return request(`/api/series/projects/${projectId}/publications`)
}

export function quoteShotCandidateRegeneration(
  shotId: string,
  candidateId: string,
  input: { reason: string; instruction?: string; preserve_fields?: string[]; replace_fields?: string[] },
): Promise<{ quote_id: string; price: string; currency: string; expires_at: string }> {
  return request(`/api/series/shots/${shotId}/candidates/${candidateId}/regeneration-quote`, { method: 'POST', ...json(input) })
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
