import { ArrowLeft, Sparkles } from 'lucide-react'
import { Link, Outlet, useLocation, useParams } from 'react-router-dom'
import type { ProjectSummary, StepKey, WorkspaceStep } from '../../api/workspace'
import type { ProjectOutletContext } from './outletContext'
import { paths } from '../../routePaths'
import { yuan, yuanCap } from '../workspace/format'
import { STEPS, stepMeta } from './steps'
import { useProjectSummary } from './useProjectSummary'
import './ProjectFrame.css'

function RunningPill({ count }: { count: number }) {
  return (
    <span className="pf-pill" data-running={count > 0}>
      <span className="pf-pill__dot" aria-hidden="true" />
      {count > 0 ? `${count} 个任务生成中` : '没有进行中的任务'}
    </span>
  )
}

function BudgetPill({ budget }: { budget: ProjectSummary['budget'] }) {
  return (
    <span className="pf-pill">
      今日 {yuan(budget.spent_today)}
      {budget.cap && ` / ${yuanCap(budget.cap)}`}
    </span>
  )
}

function StepNav({
  canvasId,
  current,
  summary,
}: {
  canvasId: string
  current: StepKey | undefined
  summary: ProjectSummary | null
}) {
  const byKey = new Map<StepKey, WorkspaceStep>((summary?.steps ?? []).map((step) => [step.key, step]))
  return (
    <nav className="pf-steps" aria-label="制作步骤">
      <ol className="pf-steps__list">
        {STEPS.map((meta, index) => {
          const step = byKey.get(meta.key)
          const status = step?.status ?? 'todo'
          const label = step?.label ?? meta.label
          return (
            <li key={meta.key}>
              <Link
                to={paths.projectStep(canvasId, meta.key)}
                className={`pf-step pf-step--${status}${current === meta.key ? ' pf-step--current' : ''}`}
                aria-current={current === meta.key ? 'step' : undefined}
              >
                {status === 'done' ? `✓ ${label}` : `${index + 1} ${label}`}
              </Link>
            </li>
          )
        })}
      </ol>
      {summary && (
        <span className="pf-steps__cost">
          本集成本 {yuan(summary.total_cost)} · {summary.shots_total} 镜头
        </span>
      )}
    </nav>
  )
}

/** 项目页框架：顶栏 + （步骤视图下的）步骤条与底栏，中间是当前视图。 */
export default function ProjectFrame() {
  const { canvasId = '' } = useParams()
  const location = useLocation()
  const { data: summary, error, errorStatus, loading, retry } = useProjectSummary(canvasId)

  const segments = location.pathname.split('/').filter(Boolean) // ['studio', id, view]
  const view = segments[2]
  const canvasView = view === 'canvas'
  const current = stepMeta(view)
  const notFound = errorStatus === 404
  // “步骤”切换去后端判定的进行中步骤；数据还没回来时去第一步
  const stepsTarget = paths.projectStep(canvasId, summary?.active_step ?? 'script')
  const next = current ? STEPS[STEPS.findIndex((step) => step.key === current.key) + 1] : undefined
  const hint = summary?.next_hint
  // 分镜页要全部镜头完成才能去配音；摘要没回来之前也先禁用
  const remainingShots = summary ? summary.shots_total - summary.shots_done : 0
  const storyboardBlocked = current?.key === 'storyboard' && (!summary || summary.shots_total === 0 || remainingShots > 0)
  const blockedNote = !summary ? '' : summary.shots_total === 0 ? '还没有镜头' : `还有 ${remainingShots} 个镜头没完成`
  const outletContext: ProjectOutletContext = { summary, loading, refreshSummary: retry }

  if (notFound) {
    return (
      <div className="pf-message" role="alert">
        <p>项目不存在或无权访问</p>
        <Link to={paths.home}>返回工作台</Link>
      </div>
    )
  }

  return (
    <div className="pf">
      <header className="pf-header">
        <div className="pf-header__left">
          <Link to={paths.home} className="pf-back" aria-label="返回工作台">
            <ArrowLeft size={18} aria-hidden="true" />
          </Link>
          {summary ? (
            <span className="pf-title">{summary.title}</span>
          ) : (
            <span className="pf-skeleton pf-skeleton--title" aria-busy={loading} aria-label="项目加载中" />
          )}
          <span className="pf-episode">第 1 集</span>
        </div>
        <div className="pf-seg" role="group" aria-label="视图切换">
          <Link
            to={stepsTarget}
            className={`pf-seg__item${canvasView ? '' : ' pf-seg__item--on'}`}
            aria-current={canvasView ? undefined : 'page'}
          >
            步骤
          </Link>
          <Link
            to={paths.projectCanvas(canvasId)}
            className={`pf-seg__item${canvasView ? ' pf-seg__item--on' : ''}`}
            aria-current={canvasView ? 'page' : undefined}
          >
            画布
          </Link>
        </div>
        <div className="pf-header__right">
          {summary ? (
            <>
              <RunningPill count={summary.running_count} />
              <BudgetPill budget={summary.budget} />
            </>
          ) : (
            <>
              <span className="pf-skeleton pf-skeleton--pill" aria-hidden="true" />
              <span className="pf-skeleton pf-skeleton--pill" aria-hidden="true" />
            </>
          )}
          <button type="button" className="pf-icon-button" disabled title="AI 助手即将上线" aria-label="AI 助手">
            <Sparkles size={16} aria-hidden="true" />
          </button>
        </div>
      </header>

      {error && !summary && (
        <div className="pf-banner" role="alert">
          <span>{error}</span>
          <button type="button" onClick={retry}>重试</button>
        </div>
      )}

      {!canvasView && <StepNav canvasId={canvasId} current={current?.key} summary={summary} />}

      <div className={`pf-body${canvasView ? ' pf-body--canvas' : ''}`}>
        <Outlet context={outletContext} />
      </div>

      {!canvasView && (
        <footer className="pf-footer">
          <span className="pf-footer__hint">
            {hint && (
              <>
                <span className="pf-footer__ai" aria-hidden="true">AI</span>
                {hint}
              </>
            )}
          </span>
          {current?.nextLabel && next && storyboardBlocked && (
            <span className="pf-next-wrap">
              {blockedNote && <span className="pf-next-note">{blockedNote}</span>}
              <button type="button" className="pf-next" disabled>
                {current.nextLabel}
              </button>
            </span>
          )}
          {current?.nextLabel && next && !storyboardBlocked && (
            <Link to={paths.projectStep(canvasId, next.key)} className="pf-next">
              {current.nextLabel}
            </Link>
          )}
        </footer>
      )}
    </div>
  )
}
