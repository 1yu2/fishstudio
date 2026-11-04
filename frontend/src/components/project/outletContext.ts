import type { ProjectSummary } from '../../api/workspace'

/** ProjectFrame 通过 Outlet 传给步骤页的上下文：项目摘要与立即刷新摘要（步骤页改动后让步骤条 / 底栏尽快更新）。 */
export interface ProjectOutletContext {
  summary: ProjectSummary | null
  loading: boolean
  refreshSummary: () => void
}
