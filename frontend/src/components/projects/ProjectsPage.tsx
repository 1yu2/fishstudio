import { useEffect, useMemo, useState } from 'react'
import { Clock, Network, Palette, Plus, Search } from 'lucide-react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { apiFetch } from '../../api'
import { nodeCanvasApi, type CanvasSummary as NodeCanvasSummary } from '../../api/nodeCanvas'
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

type ProjectFilter = 'all' | 'node' | 'freeform'

export default function ProjectsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const initialType = searchParams.get('type')
  const [filter, setFilter] = useState<ProjectFilter>(initialType === 'freeform' || initialType === 'node' ? initialType : 'all')
  const navigate = useNavigate()
  const [nodeCanvases, setNodeCanvases] = useState<NodeCanvasSummary[]>([])
  const [creating, setCreating] = useState(false)
  const [query, setQuery] = useState('')
  const [canvases, setCanvases] = useState<CanvasProject[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      const [nodeResult, canvasResult] = await Promise.allSettled([
        nodeCanvasApi.list(),
        apiFetch('/api/canvases').then(async (response) => {
          if (!response.ok) throw new Error('自由画布加载失败')
          const data = await response.json()
          return Array.isArray(data) ? data as CanvasProject[] : []
        }),
      ])

      if (nodeResult.status === 'fulfilled') setNodeCanvases(nodeResult.value)
      else toast('节点画布加载失败', 'error')
      if (canvasResult.status === 'fulfilled') setCanvases(canvasResult.value)
      else toast('自由画布项目加载失败', 'error')
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
  const visibleNodeCanvases = useMemo(
    () => nodeCanvases.filter((item) => !normalizedQuery || item.title.toLowerCase().includes(normalizedQuery)),
    [nodeCanvases, normalizedQuery],
  )
  const createNodeCanvas = async () => {
    if (creating) return
    setCreating(true)
    try {
      const canvas = await nodeCanvasApi.create({ title: `短片 ${nodeCanvases.length + 1}` })
      navigate(paths.nodeCanvas(canvas.id))
    } catch (error) {
      toast(error instanceof Error ? error.message : '创建失败', 'error')
      setCreating(false)
    }
  }

  return (
    <div className="projects-page">
      <div className="projects-page__toolbar">
        <div className="projects-page__filters" aria-label="项目类型">
          <button className={filter === 'all' ? 'active' : ''} onClick={() => selectFilter('all')}>全部</button>
          <button className={filter === 'node' ? 'active' : ''} onClick={() => selectFilter('node')}>节点画布</button>
          <button className={filter === 'freeform' ? 'active' : ''} onClick={() => selectFilter('freeform')}>Freeform / 自由画布</button>
        </div>
        <div className="projects-page__toolbar-actions">
          <button className="projects-page__create" disabled={creating} onClick={() => void createNodeCanvas()}><Plus size={15} />新建短片</button>
          <label className="projects-page__search">
            <Search size={16} />
            <input aria-label="搜索项目" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索项目" />
          </label>
        </div>
      </div>

      {loading ? <div className="projects-page__empty">项目加载中…</div> : (
        <>
          {(filter === 'all' || filter === 'node') && (
            <section className="projects-page__section">
              <div className="projects-page__section-heading">
                <div><Network size={17} /><h2>节点画布</h2></div>
                <span>{visibleNodeCanvases.length}</span>
              </div>
              <div className="projects-page__grid">
                {visibleNodeCanvases.map((canvas) => (
                  <Link className="project-card" to={paths.nodeCanvas(canvas.id)} key={canvas.id}>
                    <span className="project-card__type project-card__type--node">节点画布 · {canvas.aspect_ratio}</span>
                    <h3>{canvas.title || '未命名短片'}</h3>
                    <div className="project-card__meta"><Clock size={14} />{canvas.updated_at ? new Date(canvas.updated_at).toLocaleString() : canvas.id} · {canvas.node_count} 个节点</div>
                  </Link>
                ))}
                {visibleNodeCanvases.length === 0 && <div className="projects-page__empty">还没有节点画布，点击「新建短片」开始</div>}
              </div>
            </section>
          )}

          {(filter === 'all' || filter === 'freeform') && (
            <section className="projects-page__section">
              <div className="projects-page__section-heading">
                <div><Palette size={17} /><h2>Freeform / 自由画布（旧版）</h2></div>
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

        </>
      )}
    </div>
  )
}
