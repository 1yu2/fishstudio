import type { ProjectSummary } from '../../api/workspace'

/** 步骤页对底栏的覆盖：有值时底栏右侧主按钮用它，hint 替换左侧 AI 提示；页面卸载时必须清掉（传 null）。 */
export interface FooterOverride {
  label: string
  onClick: () => void
  disabled?: boolean
  /** 进行中：按钮禁用并显示转圈 */
  busy?: boolean
  /** 显示在按钮旁的说明，如“先写剧本” */
  note?: string
  /** 替换左侧的 AI 提示 */
  hint?: string
}

/** ProjectFrame 通过 Outlet 传给步骤页的上下文：项目摘要、立即刷新摘要（步骤页改动后让步骤条 / 底栏尽快更新）与底栏覆盖。 */
export interface ProjectOutletContext {
  summary: ProjectSummary | null
  loading: boolean
  refreshSummary: () => void
  setFooter: (override: FooterOverride | null) => void
}
