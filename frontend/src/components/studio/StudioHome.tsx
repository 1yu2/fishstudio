import { useEffect, useState } from 'react'
import { ArrowRight, Film } from 'lucide-react'
import { PageHeader } from '../ui/PageHeader'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { SeriesProject } from '../../api/series'
import { goToStudioProject } from './navigation'
import './studio.css'

type ThemeMode = 'dark' | 'light'

export default function StudioHome({
  theme,
  onToggleTheme,
}: {
  theme: ThemeMode
  onToggleTheme: () => void
}) {
  const [projects, setProjects] = useState<SeriesProject[]>([])
  const [loading, setLoading] = useState(true)
  const [title, setTitle] = useState('')
  const [goal, setGoal] = useState('')
  const [mode, setMode] = useState<'quick' | 'professional'>('quick')
  const [style, setStyle] = useState('')
  const [durationSeconds, setDurationSeconds] = useState(90)
  const [episodeCount, setEpisodeCount] = useState(1)
  const [budget, setBudget] = useState(100)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const list = await seriesApi.listProjects({ project_type: 'short_drama' })
        setProjects(Array.isArray(list) ? list : [])
      } catch (e) {
        console.error(e)
        toast('项目加载失败', 'error')
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [])

  const createProject = async () => {
    const t = title.trim()
    if (!t || creating) return
    setCreating(true)
    try {
      const result = await seriesApi.startStudioProject({
        title: t,
        mode,
        story: goal.trim(),
        style: style.trim(),
        target_duration_ms: durationSeconds * 1000,
        episode_count: episodeCount,
        budget_cny: budget,
      })
      goToStudioProject(result.project.id)
    } catch (e) {
      console.error(e)
      toast(e instanceof Error ? e.message : '创建失败', 'error')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="studio">
      <PageHeader title="短剧工作台" subtitle="系列视频 · 从大纲到成片" theme={theme} onToggleTheme={onToggleTheme} variant="home" />
      <main className="studio__main">
        <section className="studio__card studio-home__create">
          <div className="studio__section-title" style={{ margin: 0 }}>
            新建短剧
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className={`studio__btn${mode === 'quick' ? ' studio__btn--primary' : ''}`} onClick={() => setMode('quick')}>
              快速创作
            </button>
            <button className={`studio__btn${mode === 'professional' ? ' studio__btn--primary' : ''}`} onClick={() => setMode('professional')}>
              专业制作
            </button>
          </div>
          <input
            className="studio__input"
            placeholder="短剧标题，例如：午夜便利店 第一季"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <textarea
            className="studio__textarea"
            rows={2}
            placeholder="故事梗概 / 创作目标"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
          />
          <input className="studio__input" placeholder="视觉风格，例如：电影感、暖色霓虹" value={style} onChange={(e) => setStyle(e.target.value)} />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 10 }}>
            <label className="studio__muted">单集秒数<input className="studio__input" type="number" min={1} value={durationSeconds} onChange={(e) => setDurationSeconds(Math.max(1, Number(e.target.value)))} /></label>
            <label className="studio__muted">集数<input className="studio__input" type="number" min={1} max={100} value={episodeCount} onChange={(e) => setEpisodeCount(Math.max(1, Number(e.target.value)))} /></label>
            <label className="studio__muted">预算（CNY）<input className="studio__input" type="number" min={0} value={budget} onChange={(e) => setBudget(Math.max(0, Number(e.target.value)))} /></label>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button className="studio__btn studio__btn--primary" onClick={createProject} disabled={creating || !title.trim()}>
              创建并进入{mode === 'quick' ? '快速模式' : '专业工作台'}
              <ArrowRight size={14} />
            </button>
          </div>
        </section>

        <div className="studio__section-title">我的短剧项目</div>
        {loading ? (
          <div className="studio__muted">加载中…</div>
        ) : projects.length === 0 ? (
          <div className="studio__muted">还没有短剧项目，从上方创建一个吧。</div>
        ) : (
          <div className="studio-home__grid">
            {projects.map((p) => (
              <button key={p.id} className="studio__card" style={{ textAlign: 'left', cursor: 'pointer' }} onClick={() => goToStudioProject(p.id)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <Film size={16} />
                  <b style={{ fontSize: 14 }}>{p.title}</b>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span className="studio__badge">{p.status || 'draft'}</span>
                  <span className="studio__muted">
                    {p.updated_at ? new Date(p.updated_at).toLocaleString() : p.created_at ? new Date(p.created_at).toLocaleString() : ''}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </main>
    </div>
  )
}
