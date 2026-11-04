import { useMemo, useState } from 'react'
import { Plus, Search } from 'lucide-react'
import { Link } from 'react-router-dom'
import { paths } from '../../routePaths'
import { relativeTime, yuan } from '../workspace/format'
import NewDramaDialog from '../workspace/NewDramaDialog'
import { ProjectCover, StepBar, stepLine } from '../workspace/ProjectParts'
import { useWorkspaceOverview } from '../workspace/useWorkspaceOverview'
import '../workspace/WorkspacePage.css'

/** 我的项目：漫剧项目卡片网格（数据复用工作台总览接口，最近 20 个）。 */
export default function ProjectsPage() {
  const { data, error, loading, retry } = useWorkspaceOverview()
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)

  const projects = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    return (data?.projects ?? []).filter((item) => !keyword || item.title.toLowerCase().includes(keyword))
  }, [data, query])

  return (
    <div className="ws">
      <header className="ws-head">
        <h1>我的项目</h1>
        <div className="ws-head__actions">
          <label className="ws-pill">
            <Search size={14} />
            <input
              aria-label="搜索项目"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索项目"
              style={{ border: 0, outline: 0, background: 'transparent', font: 'inherit', width: 140 }}
            />
          </label>
          <button type="button" className="ws-primary" onClick={() => setCreating(true)}>
            <Plus size={15} />新建漫剧
          </button>
        </div>
      </header>

      {loading && !data && <div className="ws-state" aria-busy="true">项目加载中…</div>}
      {error && !data && (
        <div className="ws-state" role="alert">
          <p>{error}</p>
          <button type="button" className="ws-btn" onClick={retry}>重试</button>
        </div>
      )}
      {data && data.projects.length === 0 && (
        <div className="ws-state ws-state--empty">
          <h2>还没有项目</h2>
          <p>点击「新建漫剧」，从一个故事想法开始。</p>
        </div>
      )}
      {data && data.projects.length > 0 && projects.length === 0 && <div className="ws-state">没有匹配的项目</div>}

      {projects.length > 0 && (
        <div className="ws-grid">
          {projects.map((project) => (
            <Link key={project.canvas_id} className="ws-grid__card" to={paths.nodeCanvas(project.canvas_id)}>
              <div className="ws-grid__top">
                <ProjectCover project={project} />
                <div className="ws-grid__meta">
                  <strong>{project.title || '未命名漫剧'}</strong>
                  <span className="ws-muted">{project.aspect_ratio} · {relativeTime(project.updated_at)}</span>
                  <span className="ws-muted">累计 {yuan(project.total_cost)}</span>
                </div>
              </div>
              <StepBar project={project} />
              <span className="ws-muted">{stepLine(project)}</span>
            </Link>
          ))}
        </div>
      )}

      {creating && <NewDramaDialog onClose={() => setCreating(false)} />}
    </div>
  )
}
