import { apiFetch } from '../api'

export interface WorkspaceBudget {
  /** 今日云端花费上限（金额字符串）；null 表示不限 */
  cap: string | null
  spent_today: string
  in_flight: string
  remaining: string | null
}

export type StepKey = 'script' | 'characters' | 'storyboard' | 'voice' | 'final'

export interface WorkspaceStep {
  key: StepKey
  status: 'done' | 'active' | 'todo'
  label: string
}

export interface WorkspaceProject {
  canvas_id: string
  project_id: string
  title: string
  aspect_ratio: string
  updated_at: string | null
  cover_artifact_id: string | null
  total_cost: string
  shots_total: number
  shots_done: number
  steps: WorkspaceStep[]
  next_hint: string
}

export interface WorkspaceRunning {
  job_id: string
  canvas_id: string
  project_title: string
  node_label: string
  kind: 'image' | 'video'
  status: 'queued' | 'running'
  progress: number
  created_at: string | null
}

export interface WorkspaceReview {
  canvas_id: string
  project_title: string
  node_id: string
  node_label: string
  reason: 'new_result' | 'no_selection'
}

export interface WorkspaceFailed {
  job_id: string
  canvas_id: string
  project_title: string
  node_label: string
  error: string
}

export interface WorkspaceOverview {
  budget: WorkspaceBudget
  counts: { running: number; needs_review: number; finished_this_week: number; failed: number }
  projects: WorkspaceProject[]
  running: WorkspaceRunning[]
  needs_review: WorkspaceReview[]
  failed: WorkspaceFailed[]
}

export const workspaceApi = {
  overview: async (): Promise<WorkspaceOverview> => {
    const response = await apiFetch('/api/workspace/overview')
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw new Error(body?.error?.message || (typeof body?.detail === 'string' ? body.detail : '') || '工作台加载失败')
    }
    return response.json()
  },
}
