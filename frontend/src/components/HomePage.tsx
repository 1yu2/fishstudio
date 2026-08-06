import { useEffect, useMemo, useState, useRef } from 'react'
import './HomePage.css'
import { ArrowRight, Clock, LayoutGrid, List, Search, Paperclip, X, Star, Trash2 } from 'lucide-react'
import { apiFetch } from '../api'
import { usePreferences, toggleFavorite } from '../utils/preferences'
import { PageHeader } from './ui/PageHeader'
import { Greeting } from './ui/Greeting'
import { toast } from './ui/Toast'
import { confirmDialog } from './ui/ConfirmDialog'
import { goToStudioHome } from './studio/navigation'

type ThemeMode = 'dark' | 'light'

type CanvasSummary = {
  id: string
  name?: string
  createdAt?: number
  images?: unknown[]
  messages?: unknown[]
  data?: {
    elements?: any[]
    files?: Record<string, { dataURL?: string; mimeType?: string }>
  }
}

function getCanvasIdFromUrl() {
  try {
    const url = new URL(window.location.href)
    return url.searchParams.get('canvasId') || ''
  } catch {
    return ''
  }
}

function setCanvasIdToUrl(canvasId: string) {
  const url = new URL(window.location.href)
  url.searchParams.set('canvasId', canvasId)
  window.history.pushState({}, '', url.toString())
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function clearCanvasIdFromUrl() {
  const url = new URL(window.location.href)
  url.searchParams.delete('canvasId')
  window.history.pushState({}, '', url.toString())
  window.dispatchEvent(new PopStateEvent('popstate'))
}

function pickThumbnails(canvas: CanvasSummary, max = 4): string[] {
  const files = canvas.data?.files || {}
  const urls = Object.values(files)
    .map((f) => (typeof f?.dataURL === 'string' ? f.dataURL : null))
    .filter((u): u is string => Boolean(u))
  // 去重 & 截断（最多展示 3 张拼贴预览）
  const uniq = Array.from(new Set(urls))
  return uniq.slice(0, max)
}

function countImages(canvas: CanvasSummary): number {
  const els = canvas.data?.elements || []
  const fromElements = els.filter((e) => e && !e.isDeleted && e.type === 'image').length
  if (fromElements) return fromElements
  return Array.isArray(canvas.images) ? canvas.images.length : 0
}

function extractTextPreview(canvas: CanvasSummary, maxLen = 84): string {
  const raw = Array.isArray(canvas.messages) ? canvas.messages : []
  // 从最新开始找一条“有可读文本”的消息
  for (let i = raw.length - 1; i >= 0; i--) {
    const m = raw[i] as any
    if (!m || typeof m !== 'object') continue
    const post = typeof m.postToolContent === 'string' ? m.postToolContent : ''
    const content = typeof m.content === 'string' ? m.content : ''
    let text = (post || content || '').trim()
    if (!text) continue

    // 清理掉图片标记/URL 列表，保留纯文本
    const lines = text
      .split('\n')
      .map((l: string) => l.trim())
      .filter(Boolean)
      .filter((l: string) => !l.startsWith('[图片:'))
      .filter((l: string) => l !== 'Generated Image:')
      .filter((l: string) => !(l.startsWith('- ') && (l.includes('/storage/') || l.includes('http://') || l.includes('https://'))))

    text = lines.join(' ').replace(/\s+/g, ' ').trim()
    if (!text) continue

    if (text.length > maxLen) return text.slice(0, maxLen) + '…'
    return text
  }
  return ''
}

const CAPABILITY_CHIPS = [
  { label: '🖼 文生图', prompt: '帮我生成一张图片：' },
  { label: '🎬 视频', prompt: '帮我生成一段视频：' },
  { label: '🧊 3D 模型', prompt: '帮我生成一个 3D 模型：' },
  { label: '🧑‍🎤 虚拟人', prompt: '用我上传的图片和音频生成虚拟人口型同步视频' },
  { label: '🎙 TTS', prompt: '把这段文字转成语音：' },
  { label: '📝 文案', prompt: '帮我写一篇小红书文案：' },
]

function groupByTime(projects: CanvasSummary[]): Array<{ label: string; items: CanvasSummary[] }> {
  const now = new Date()
  const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfWeek = startOfDay - ((now.getDay() + 6) % 7) * 86400000 // 周一为一周起点
  const groups = [
    { label: '今天', items: [] as CanvasSummary[] },
    { label: '本周', items: [] as CanvasSummary[] },
    { label: '更早', items: [] as CanvasSummary[] },
  ]
  for (const p of projects) {
    const t = p.createdAt ?? 0
    if (t >= startOfDay) groups[0].items.push(p)
    else if (t >= startOfWeek) groups[1].items.push(p)
    else groups[2].items.push(p)
  }
  return groups.filter((g) => g.items.length > 0)
}

/** 项目状态：最近一条含工具调用的消息中仍有 executing 调用则视为"进行中" */
function projectStatus(canvas: CanvasSummary): 'running' | 'done' {
  const msgs = Array.isArray(canvas.messages) ? canvas.messages : []
  for (let i = msgs.length - 1; i >= 0; i--) {
    const toolCalls = (msgs[i] as any)?.toolCalls
    if (Array.isArray(toolCalls) && toolCalls.length > 0) {
      return toolCalls.some((tc: any) => tc?.status === 'executing') ? 'running' : 'done'
    }
  }
  return 'done'
}

export default function HomePage({
  theme,
  onToggleTheme,
}: {
  theme: ThemeMode
  onToggleTheme: () => void
}) {
  const [projects, setProjects] = useState<CanvasSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [prompt, setPrompt] = useState('')
  const [uploadedImages, setUploadedImages] = useState<string[]>([]) // 上传的图片URL列表
  const [uploadedAudios, setUploadedAudios] = useState<string[]>([]) // 上传的音频URL列表
  const [creating, setCreating] = useState(false)
  const [prefs, setPrefs] = usePreferences()
  const [filter, setFilter] = useState<'all' | 'fav'>('all')
  const promptRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    // If already has canvasId (e.g. user pasted link), redirect to board
    const existing = getCanvasIdFromUrl()
    if (existing) return
    clearCanvasIdFromUrl()
  }, [])

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true)
        const res = await apiFetch('/api/canvases')
        if (!res.ok) throw new Error('Failed to load projects')
        const data = (await res.json()) as CanvasSummary[]
        setProjects(Array.isArray(data) ? data : [])
      } catch (e) {
        console.error(e)
        toast('项目加载失败，请稍后重试', 'error')
        setProjects([])
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return projects
    return projects.filter((p) => (p.name || '').toLowerCase().includes(q) || p.id.toLowerCase().includes(q))
  }, [projects, query])

  const sorted = useMemo(() => {
    const arr = [...filtered]
    arr.sort((a, b) =>
      prefs.homeSort === 'newest' ? (b.createdAt ?? 0) - (a.createdAt ?? 0) : (a.createdAt ?? 0) - (b.createdAt ?? 0),
    )
    return arr
  }, [filtered, prefs.homeSort])

  const visible = useMemo(
    () => (filter === 'fav' ? sorted.filter((p) => prefs.favorites.includes(p.id)) : sorted),
    [sorted, filter, prefs.favorites],
  )

  const groups = useMemo(() => groupByTime(visible), [visible])

  const monthCount = useMemo(() => {
    const now = new Date()
    return projects.filter((p) => {
      if (!p.createdAt) return false
      const d = new Date(p.createdAt)
      return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()
    }).length
  }, [projects])

  const onToggleFav = (e: React.MouseEvent, projectId: string) => {
    e.stopPropagation()
    toggleFavorite(projectId)
  }

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    // 判断是图片还是音频
    const isImage = file.type.startsWith('image/')
    const isAudio = file.type.startsWith('audio/') ||
                    ['.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.wma'].some(ext =>
                      file.name.toLowerCase().endsWith(ext)
                    )

    if (!isImage && !isAudio) {
      toast('只支持图片或音频文件', 'error')
      return
    }

    try {
      const formData = new FormData()
      formData.append('file', file)

      const endpoint = isImage ? '/api/upload-image' : '/api/upload-audio'
      const response = await apiFetch(endpoint, {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        throw new Error('上传失败')
      }

      const data = await response.json()
      if (isImage) {
        setUploadedImages(prev => [...prev, data.url])
      } else {
        setUploadedAudios(prev => [...prev, data.url])
      }
    } catch (error) {
      console.error('文件上传失败:', error)
      toast('文件上传失败，请重试', 'error')
    } finally {
      // 清空文件选择，允许重复选择同一文件
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    }
  }

  const removeUploadedImage = (index: number) => {
    setUploadedImages(prev => prev.filter((_, i) => i !== index))
  }

  const removeUploadedAudio = (index: number) => {
    setUploadedAudios(prev => prev.filter((_, i) => i !== index))
  }

  const deleteProject = async (e: React.MouseEvent, projectId: string) => {
    e.stopPropagation() // 阻止点击卡片跳转
    const ok = await confirmDialog({
      title: '删除该项目？',
      description: '删除后无法恢复，画布内容将一并清除。',
      confirmText: '删除',
      danger: true,
    })
    if (!ok) return
    try {
      await apiFetch(`/api/canvases/${projectId}`, { method: 'DELETE' })
      setProjects((prev) => prev.filter((p) => p.id !== projectId))
      toast('已删除项目', 'success')
    } catch (err) {
      console.error('删除项目失败', err)
      toast('删除失败，请重试', 'error')
    }
  }

  const createProjectAndEnter = async (firstPrompt?: string, images?: string[], audios?: string[]) => {
    if (creating) return
    setCreating(true)
    try {
      const id = `canvas-${Date.now()}`
      const nameFromPrompt = (firstPrompt || '').trim().slice(0, 18)
      const payload: CanvasSummary = {
        id,
        name: nameFromPrompt ? `项目：${nameFromPrompt}` : `项目 ${projects.length + 1}`,
        createdAt: Date.now(),
        images: [],
        data: { elements: [], files: {}, },
        messages: [],
      }
      await apiFetch('/api/canvases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      // 构建消息内容：文本 + 图片URL + 音频URL
      let messageContent = (firstPrompt || '').trim()
      if (images && images.length > 0) {
        const imageTexts = images.map(url => `[图片: ${url}]`).join('\n')
        if (messageContent) {
          messageContent = `${messageContent}\n\n${imageTexts}`
        } else {
          messageContent = imageTexts
        }
      }
      if (audios && audios.length > 0) {
        const audioTexts = audios.map(url => `[音频: ${url}]`).join('\n')
        if (messageContent) {
          messageContent = `${messageContent}\n\n${audioTexts}`
        } else {
          messageContent = audioTexts
        }
      }

      if (messageContent) {
        // 存储完整的消息内容（包括图片和音频）
        sessionStorage.setItem(`pending_prompt:${id}`, messageContent)
        // 同时存储图片URL列表，用于在对话界面显示
        if (images && images.length > 0) {
          sessionStorage.setItem(`pending_images:${id}`, JSON.stringify(images))
        }
        // 同时存储音频URL列表，用于在对话界面显示
        if (audios && audios.length > 0) {
          sessionStorage.setItem(`pending_audios:${id}`, JSON.stringify(audios))
        }
      }
      setCanvasIdToUrl(id)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="home">
      <PageHeader title="FishStudio" subtitle="创意不止于画布" theme={theme} onToggleTheme={onToggleTheme} variant="home" />

      <main className="home__main">
        <section className="home__top">
          <Greeting nickname={prefs.nickname} />
          <div className="home__stats">
            <div className="home__stat">
              <b>{projects.length}</b>
              <span>项目</span>
            </div>
            <div className="home__stat">
              <b>{monthCount}</b>
              <span>本月新建</span>
            </div>
            <div className="home__stat">
              <b>{prefs.favorites.length}</b>
              <span>收藏</span>
            </div>
          </div>
        </section>

        <section className="home__promptCard">
          {uploadedImages.length > 0 && (
            <div className="home__uploaded-images">
              {uploadedImages.map((url, index) => (
                <div key={index} className="home__uploaded-image-item">
                  <img src={url} alt={`上传的图片 ${index + 1}`} />
                  <button className="home__remove-image-btn" onClick={() => removeUploadedImage(index)} title="移除图片">
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
          {uploadedAudios.length > 0 && (
            <div className="home__uploaded-images">
              {uploadedAudios.map((url, index) => (
                <div key={index} className="home__uploaded-image-item">
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', maxWidth: 200, wordBreak: 'break-all' }}>
                    音频 {index + 1}: {url.split('/').pop()}
                  </div>
                  <button className="home__remove-image-btn" onClick={() => removeUploadedAudio(index)} title="移除音频">
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="home__input-row">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*,audio/*"
              onChange={handleFileUpload}
              style={{ display: 'none' }}
            />
            <button className="home__upload-btn" onClick={() => fileInputRef.current?.click()} disabled={creating} title="上传图片或音频">
              <Paperclip size={18} />
            </button>
            <textarea
              ref={promptRef}
              className="home__promptInput"
              placeholder="例如：生成一套 12 生肖毛绒吉祥物，每个生肖一张图，统一风格…"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  // 回车键（非 Shift+Enter）触发创建项目
                  e.preventDefault()
                  if (!creating && (prompt.trim() || uploadedImages.length > 0 || uploadedAudios.length > 0)) {
                    createProjectAndEnter(prompt, uploadedImages, uploadedAudios)
                  }
                }
              }}
              onPaste={(e) => {
                try {
                  const items = e.clipboardData?.items
                  if (!items) return

                  for (let i = 0; i < items.length; i++) {
                    const item = items[i]
                    const isImage = item.type.indexOf('image') !== -1
                    const isAudio = item.type.indexOf('audio') !== -1
                    if (isImage || isAudio) {
                      e.preventDefault()
                      const file = item.getAsFile()
                      if (!file) continue

                      // 异步上传，但不阻塞
                      ;(async () => {
                        try {
                          const formData = new FormData()
                          formData.append('file', file)

                          const endpoint = isImage ? '/api/upload-image' : '/api/upload-audio'
                          const response = await apiFetch(endpoint, {
                            method: 'POST',
                            body: formData,
                          })

                          if (!response.ok) {
                            throw new Error('上传失败')
                          }

                          const data = await response.json()
                          if (isImage) {
                            setUploadedImages(prev => [...prev, data.url])
                          } else {
                            setUploadedAudios(prev => [...prev, data.url])
                          }
                        } catch (error) {
                          console.error('文件粘贴上传失败:', error)
                          toast('文件粘贴上传失败，请重试', 'error')
                        }
                      })()
                      break // 只处理第一个文件
                    }
                  }
                } catch (error) {
                  console.error('粘贴处理错误:', error)
                }
              }}
              rows={3}
            />
          </div>
          <div className="home__promptFooter">
            <div className="home__hint">按回车键或点击右侧按钮创建项目并进入画板</div>
            <button
              className="home__btn home__btn--primary"
              onClick={() => createProjectAndEnter(prompt, uploadedImages, uploadedAudios)}
              disabled={creating || (!prompt.trim() && uploadedImages.length === 0 && uploadedAudios.length === 0)}
            >
              开始
              <ArrowRight size={16} />
            </button>
          </div>
        </section>

        <div className="home__chips">
          {CAPABILITY_CHIPS.map((c) => (
            <button
              key={c.label}
              className="home__chip-btn"
              onClick={() => {
                setPrompt(c.prompt)
                promptRef.current?.focus()
              }}
            >
              {c.label}
            </button>
          ))}
        </div>

        <section className="home__list">
          <div className="home__toolbar">
            <div className="home__tabs">
              <button className={`home__tab${filter === 'all' ? ' home__tab--active' : ''}`} onClick={() => setFilter('all')}>
                全部
              </button>
              <button className={`home__tab${filter === 'fav' ? ' home__tab--active' : ''}`} onClick={() => setFilter('fav')}>
                ⭐ 收藏
              </button>
            </div>
            <div className="home__toolbar-right">
              <button className="home__select" style={{ cursor: 'pointer' }} onClick={goToStudioHome} title="短剧/系列视频工作台">
                🎬 短剧工作台
              </button>
              <div className="home__search">
                <Search size={16} />
                <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索项目名 / ID" />
              </div>
              <select
                className="home__select"
                value={prefs.homeSort}
                onChange={(e) => setPrefs({ homeSort: e.target.value as 'newest' | 'oldest' })}
                title="排序"
              >
                <option value="newest">最新优先</option>
                <option value="oldest">最早优先</option>
              </select>
              <select
                className="home__select"
                value={prefs.homeDensity}
                onChange={(e) => setPrefs({ homeDensity: e.target.value as 'comfortable' | 'compact' })}
                title="卡片密度"
              >
                <option value="comfortable">舒适</option>
                <option value="compact">紧凑</option>
              </select>
              <button
                className="home__view-btn"
                title={prefs.homeView === 'grid' ? '切换为列表视图' : '切换为网格视图'}
                onClick={() => setPrefs({ homeView: prefs.homeView === 'grid' ? 'list' : 'grid' })}
              >
                {prefs.homeView === 'grid' ? <LayoutGrid size={16} /> : <List size={16} />}
              </button>
            </div>
          </div>

          {loading ? (
            <div className="home__grid">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="home__card home__card--skeleton" />
              ))}
            </div>
          ) : (
            <>
              {groups.map((g) => (
                <section key={g.label}>
                  <div className="home__groupLabel">{g.label}</div>
                  <div className={`home__grid home__grid--${prefs.homeDensity} home__grid--${prefs.homeView}`}>
                    {g.items.map((p) => {
                      const thumbs = pickThumbnails(p)
                      const created = p.createdAt ? new Date(p.createdAt).toLocaleString() : ''
                      const n = countImages(p)
                      const preview = extractTextPreview(p)
                      const fav = prefs.favorites.includes(p.id)
                      const status = projectStatus(p)
                      return (
                        <div key={p.id} className="home__card-wrapper">
                          <button className="home__card" onClick={() => setCanvasIdToUrl(p.id)} title={p.id}>
                            <div className="home__thumb">
                              {thumbs.length > 0 ? (
                                <div className={`home__thumbGrid home__thumbGrid--${Math.min(thumbs.length, 4)}`} aria-hidden="true">
                                  {thumbs.slice(0, 4).map((src, idx) => (
                                    <img key={`${p.id}:${idx}`} className="home__thumbImg" src={src} alt="" />
                                  ))}
                                </div>
                              ) : (
                                <div className="home__thumbEmpty">
                                  {preview ? (
                                    <>
                                      <div className="home__thumbLabel">文本预览</div>
                                      <div className="home__thumbText">{preview}</div>
                                    </>
                                  ) : (
                                    'No Preview'
                                  )}
                                </div>
                              )}
                            </div>
                            <div className="home__meta">
                              <div className="home__name">{p.name || '未命名项目'}</div>
                              {preview && <div className="home__preview">{preview}</div>}
                              <div className="home__sub">
                                <span className="home__chip">
                                  <Clock size={14} />
                                  {created || p.id}
                                </span>
                                <span className="home__chip">{n} 张</span>
                                <span className={`home__chip home__chip--status-${status}`}>
                                  {status === 'running' ? '进行中' : '已完成'}
                                </span>
                              </div>
                            </div>
                          </button>
                          <button
                            className={`home__fav-btn${fav ? ' home__fav-btn--active' : ''}`}
                            onClick={(e) => onToggleFav(e, p.id)}
                            title={fav ? '取消收藏' : '收藏'}
                          >
                            <Star size={15} fill={fav ? 'currentColor' : 'none'} />
                          </button>
                          <button className="home__delete-chip" onClick={(e) => deleteProject(e, p.id)} title="删除项目">
                            <Trash2 size={14} />
                          </button>
                        </div>
                      )
                    })}
                  </div>
                </section>
              ))}
              {visible.length === 0 && (
                <div className="home__empty">
                  <div className="home__empty-icon">🎨</div>
                  {query || filter === 'fav' ? '没有匹配的项目，换个条件试试。' : '还没有项目，从上方输入框开始你的第一个创作吧。'}
                </div>
              )}
            </>
          )}
        </section>
      </main>
    </div>
  )
}
