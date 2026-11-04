import { ProtectedImage } from '../common/ProtectedMedia'
import type { WorkspaceProject } from '../../api/workspace'
import { coverColor } from './format'

/** 封面缩略图：最近的选中 / 成功图片，没有则色块。 */
export function ProjectCover({ project }: { project: WorkspaceProject }) {
  return (
    <div className="ws-cover" style={{ background: coverColor(project.canvas_id) }}>
      {project.cover_artifact_id && (
        <ProtectedImage src={`/api/artifacts/${project.cover_artifact_id}/download`} alt="" loading="lazy" />
      )}
    </div>
  )
}

const STEP_NAMES: Record<string, string> = {
  script: '剧本',
  characters: '角色',
  storyboard: '分镜',
  voice: '配音',
  final: '成片',
}

/** 五段进度条：剧本 / 角色 / 分镜 / 配音 / 成片。进行中的分镜段按镜头完成比例填充。 */
export function StepBar({ project }: { project: WorkspaceProject }) {
  const summary = project.steps
    .map((step) => `${STEP_NAMES[step.key]}${step.status === 'done' ? '已完成' : step.status === 'active' ? '进行中' : '未开始'}`)
    .join('、')
  const ratio = project.shots_total ? project.shots_done / project.shots_total : 0
  return (
    <div className="ws-steps" role="img" aria-label={`进度：${summary}`}>
      {project.steps.map((step) => {
        const fill =
          step.key === 'storyboard' && project.shots_total > 0 ? Math.round(ratio * 100) : step.status === 'done' ? 100 : 0
        return (
          <span key={step.key} className={`ws-steps__seg ws-steps__seg--${step.status}`}>
            <span style={{ width: `${fill}%` }} />
          </span>
        )
      })}
    </div>
  )
}

/** “当前步骤 · 下一步”：全部完成时提示已完成。 */
export function stepLine(project: WorkspaceProject): string {
  const active = project.steps.find((step) => step.status === 'active')
  return active ? `${active.label} · 下一步：${project.next_hint}` : '全部步骤已完成'
}
