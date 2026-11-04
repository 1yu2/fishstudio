import { useState } from 'react'
import { Plus } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { WorkspaceOverview } from '../../api/workspace'
import { paths } from '../../routePaths'
import { relativeTime, yuan, yuanCap } from './format'
import NewDramaDialog from './NewDramaDialog'
import { ProjectCover, StepBar, stepLine } from './ProjectParts'
import { useWorkspaceOverview } from './useWorkspaceOverview'
import './WorkspacePage.css'

function BudgetPill({ budget }: { budget: WorkspaceOverview['budget'] }) {
  const ratio = budget.cap ? Math.min(100, (Number(budget.spent_today) / Number(budget.cap)) * 100) : 0
  return (
    <span className="ws-pill">
      今日花费 <b>{yuan(budget.spent_today)}</b>
      {budget.cap && (
        <>
          {' '}/ {yuanCap(budget.cap)}
          <span className="ws-pill__bar" role="presentation"><span style={{ width: `${ratio}%` }} /></span>
        </>
      )}
    </span>
  )
}

function Skeleton() {
  return (
    <div className="ws-skeleton" aria-busy="true" aria-label="工作台加载中">
      <div className="ws-skeleton__row">
        {[0, 1, 2, 3].map((i) => <div key={i} className="ws-skeleton__block" style={{ height: 70 }} />)}
      </div>
      <div className="ws-skeleton__block" style={{ height: 280 }} />
    </div>
  )
}

function Panels({ data }: { data: WorkspaceOverview }) {
  const { running, needs_review: review, failed, budget } = data
  const inFlight = Number(budget.in_flight)
  return (
    <div className="ws-cols">
      <section className="ws-card ws-cols__main" aria-label="继续创作">
        <div className="ws-card__head">
          <h2>继续创作</h2>
          <Link to={paths.projects}>全部项目</Link>
        </div>
        {data.projects.map((project) => (
          <Link key={project.canvas_id} className="ws-project" to={paths.nodeCanvas(project.canvas_id)}>
            <ProjectCover project={project} />
            <div className="ws-project__body">
              <div className="ws-project__row">
                <span className="ws-project__title">{project.title || '未命名漫剧'}</span>
                <span className="ws-muted">{relativeTime(project.updated_at)}</span>
              </div>
              <StepBar project={project} />
              <div className="ws-project__row ws-muted">
                <span>{stepLine(project)}</span>
                <span>累计 {yuan(project.total_cost)}</span>
              </div>
            </div>
          </Link>
        ))}
      </section>

      <div className="ws-cols__side">
        <section className="ws-card" aria-label="正在生成">
          <div className="ws-card__head">
            <h2>正在生成</h2>
            {inFlight > 0 && <span className="ws-muted">预计 {yuan(budget.in_flight)}</span>}
          </div>
          {running.length === 0 && failed.length === 0 && <p className="ws-card__empty">暂时没有进行中的任务</p>}
          {running.map((job) => (
            <div key={job.job_id} className="ws-item ws-item--col">
              <div className="ws-item__row">
                <span>{job.project_title} · {job.node_label}</span>
                <span className="ws-muted">{job.status === 'queued' ? '排队中' : `生成中 ${job.progress}%`}</span>
              </div>
              <div className="ws-bar"><span style={{ width: `${Math.max(job.progress, job.status === 'queued' ? 4 : 8)}%` }} /></div>
            </div>
          ))}
          {failed.map((job) => (
            <div key={job.job_id} className="ws-item">
              <span className="ws-item__text" title={job.error}>
                <span className="ws-failed">失败</span> · {job.project_title} · {job.node_label}
              </span>
              <Link className="ws-btn-sm" to={paths.nodeCanvas(job.canvas_id)}>查看</Link>
            </div>
          ))}
        </section>

        <section className="ws-card" aria-label="等你确认">
          <div className="ws-card__head"><h2>等你确认</h2></div>
          {review.length === 0 && <p className="ws-card__empty">没有需要确认的事项</p>}
          {review.map((item) => (
            <Link key={`${item.canvas_id}:${item.node_id}`} className="ws-item ws-item--link" to={paths.nodeCanvas(item.canvas_id)}>
              <span className="ws-item__text">
                {item.project_title} · {item.node_label}{item.reason === 'new_result' ? ' 有新结果' : ' 待选图'}
              </span>
              <span className="ws-link">去选择</span>
            </Link>
          ))}
        </section>
      </div>
    </div>
  )
}

export default function WorkspacePage() {
  const { data, error, loading, retry } = useWorkspaceOverview()
  const [creating, setCreating] = useState(false)

  const subtitle = data ? `${data.counts.running} 个任务正在生成，${data.counts.needs_review} 项等你确认` : ' '
  const cards = data && [
    { label: '生成中', value: data.counts.running, tone: data.counts.running > 0 ? 'running' : '' },
    { label: '等你确认', value: data.counts.needs_review, tone: '' },
    { label: '本周成片', value: data.counts.finished_this_week, tone: '' },
    { label: '失败待处理', value: data.counts.failed, tone: data.counts.failed > 0 ? 'failed' : '' },
  ]

  return (
    <div className="ws">
      <header className="ws-head">
        <div>
          <h1>工作台</h1>
          <span className="ws-muted">{subtitle}</span>
        </div>
        <div className="ws-head__actions">
          {data && <BudgetPill budget={data.budget} />}
          <button type="button" className="ws-primary" onClick={() => setCreating(true)}>
            <Plus size={15} />新建漫剧
          </button>
        </div>
      </header>

      {loading && !data && <Skeleton />}

      {error && !data && (
        <div className="ws-state" role="alert">
          <p>{error}</p>
          <button type="button" className="ws-btn" onClick={retry}>重试</button>
        </div>
      )}

      {data && data.projects.length === 0 && (
        <div className="ws-state ws-state--empty">
          <h2>开始你的第一部漫剧</h2>
          <p>写下一个故事想法，AI 帮你拆成镜头、生成画面和视频。</p>
          <button type="button" className="ws-primary" onClick={() => setCreating(true)}>
            <Plus size={15} />新建漫剧
          </button>
        </div>
      )}

      {data && data.projects.length > 0 && (
        <>
          <section className="ws-stats" aria-label="概览">
            {cards?.map((card) => (
              <div key={card.label} className="ws-stat">
                <span className="ws-muted-lg">{card.label}</span>
                <span className={`ws-stat__value${card.tone ? ` ws-stat__value--${card.tone}` : ''}`}>{card.value}</span>
              </div>
            ))}
          </section>
          <Panels data={data} />
        </>
      )}

      {creating && <NewDramaDialog onClose={() => setCreating(false)} />}
    </div>
  )
}
