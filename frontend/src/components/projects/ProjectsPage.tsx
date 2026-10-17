import { useEffect, useMemo, useState } from 'react'
import { Clock, LayoutDashboard, Palette, Plus, Search } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiFetch } from '../../api'
import * as seriesApi from '../../api/series'
import type { SeriesProject } from '../../api/series'
import { paths } from '../../routePaths'
import { toast } from '../ui/Toast'
import './ProjectsPage.css'

interface CanvasProject {
  id: string
  name?: string
  createdAt?: number
  messages?: unknown[]
  images?: unknown[]
}

type ProjectFilter = 'all' | 'freeform' | 'drama'

export default function ProjectsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialType = searchParams.get('type')
  const [filter, setFilter] = useState<ProjectFilter>(initialType === 'drama' ? 'drama' : initialType === 'freeform' ? 'freeform' : 'all')
  const [query, setQuery] = useState('')
  const [canvases, setCanvases] = useState<CanvasProject[]>([])
  const [dramas, setDramas] = useState<SeriesProject[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      const [canvasResult, dramaResult] = await Promise.allSettled([
        apiFetch('/api/canvases').then(async (response) => {
          if (!response.ok) throw new Error('自由画布加载失败')
          const data = await response.json()
          return Array.isArray(data) ? data as CanvasProject[] : []
        }),
        seriesApi.listProjects({ project_type: 'short_drama' }),
      ])

      if (canvasResult.status === 'fulfilled') setCanvases(canvasResult.value)
      else toast('自由画布项目加载失败', 'error')
      if (dramaResult.status === 'fulfilled') setDramas(dramaResult.value)
      else toast('漫剧项目加载失败', 'error')
      setLoading(false)
    }
    void load()
  }, [])

  const selectFilter = (next: ProjectFilter) => {
    setFilter(next)
    if (next === 'all') setSearchParams({}, { replace: true })
    else setSearchParams({ type: next }, { replace: true })
  }

  const normalizedQuery = query.trim().toLowerCase()
  const visibleCanvases = useMemo(
    () => canvases.filter((item) => !normalizedQuery || (item.name || item.id).toLowerCase().includes(normalizedQuery)),
    [canvases, normalizedQuery],
  )
  const visibleDramas = useMemo(
    () => dramas.filter((item) => !normalizedQuery || item.title.toLowerCase().includes(normalizedQuery)),
    [dramas, normalizedQuery],
  )

  return (
    <div className="projects-page">
      <div className="projects-page__toolbar">
        <div className="projects-page__filters" aria-label="项目类型">
          <button className={filter === 'all' ? 'active' : ''} onClick={() => selectFilter('all')}>全部</button>
          <button className={filter === 'freeform' ? 'active' : ''} onClick={() => selectFilter('freeform')}>Freeform / 自由画布</button>
          <button className={filter === 'drama' ? 'active' : ''} onClick={() => selectFilter('drama')}>Drama / 漫剧</button>
        </div>
        <div className="projects-page__toolbar-actions">
          <Link className="projects-page__create" to={paths.dramaHome}><Plus size={15} />新建漫剧</Link>
          <label className="projects-page__search">
            <Search size={16} />
            <input aria-label="搜索项目" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索项目" />
          </label>
        </div>
      </div>

      {loading ? <div className="projects-page__empty">项目加载中…</div> : (
        <>
          {(filter === 'all' || filter === 'freeform') && (
            <section className="projects-page__section">
              <div className="projects-page__section-heading">
                <div><Palette size={17} /><h2>Freeform / 自由画布</h2></div>
                <span>{visibleCanvases.length}</span>
              </div>
              <div className="projects-page__grid">
                {visibleCanvases.map((project) => (
                  <Link className="project-card" to={paths.canvas(project.id)} key={project.id}>
                    <span className="project-card__type project-card__type--freeform">Freeform / 自由画布</span>
                    <h3>{project.name || '未命名画布'}</h3>
                    <div className="project-card__meta"><Clock size={14} />{project.createdAt ? new Date(project.createdAt).toLocaleString() : project.id}</div>
                  </Link>
                ))}
                {visibleCanvases.length === 0 && <div className="projects-page__empty">暂无自由画布项目</div>}
              </div>
            </section>
          )}

          {(filter === 'all' || filter === 'drama') && (
            <section className="projects-page__section">
              <div className="projects-page__section-heading">
                <div><LayoutDashboard size={17} /><h2>Drama / 漫剧</h2></div>
                <span>{visibleDramas.length}</span>
              </div>
              <div className="projects-page__grid">
                {visibleDramas.map((project) => (
                  <Link className="project-card" to={paths.dramaProject(project.id)} key={project.id}>
                    <span className="project-card__type project-card__type--drama">Drama / 漫剧</span>
                    <h3>{project.title}</h3>
                    <div className="project-card__meta">
                      <span className="project-card__status">{project.status || 'draft'}</span>
                      {project.updated_at ? new Date(project.updated_at).toLocaleString() : project.id}
                    </div>
                  </Link>
                ))}
                {visibleDramas.length === 0 && <div className="projects-page__empty">暂无漫剧项目</div>}
              </div>
            </section>
          )}
        </>
      )}
    </div>
  )
}
