import { useState, useEffect, useCallback } from 'react'
import {
  ArrowLeft,
  Sun,
  Moon,
  Server,
  FileCode2,
  Trash2,
  Plus,
  Eye,
  EyeOff,
  Save,
  Check,
  AlertCircle,
  Wrench,
  Tag,
  Zap,
  Package,
  Brain,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import './SettingsPage.css'

// ─── Types ──────────────────────────────────────────────────────────
type ThemeMode = 'dark' | 'light'
type TabId = 'workspace' | 'skills' | 'tools' | 'mcp' | 'env'
type MCPView = 'form' | 'json'

/** Tool skills (生图/生视频等，来自后端 DEFAULT_SKILLS) */
interface ToolItem {
  id: string
  name: string
  description: string
  enabled: boolean
  category: string
}

/** Installed skills (来自 SKILL.md 文件) */
interface InstalledSkill {
  id: string
  name: string
  description: string
  source: 'public' | 'custom'
  enabled: boolean
}

interface MCPServerForm {
  _key: string
  name: string
  command: string
  args: string
  envPairs: { k: string; v: string }[]
}

interface EnvItem {
  key: string
  value: string
  desc: string
  sensitive: boolean
}

interface EnvGroup {
  group: string
  items: EnvItem[]
}

// ─── Tool Skill 详情元数据 ────────────────────────────────────────────
interface ToolDetail {
  fullDesc: string
  whenToUse: string
  tools: { name: string; desc: string }[]
  tags: string[]
  iconBg: string
  iconChar: string
}

const TOOL_DETAILS: Record<string, ToolDetail> = {
  image_generation: {
    fullDesc:
      '基于火山引擎 Doubao 模型，支持文字生成图片和图片编辑两种模式。可以根据自然语言描述生成高质量图片，也可以对现有图片进行风格迁移、局部修改等操作。',
    whenToUse:
      '当用户要求「画一张…」「生成图片…」「把这张图改成…」等与图片创作相关的指令时，AI 会自动调用此工具。',
    tools: [
      { name: 'generate_image', desc: '文字 → 图片，支持尺寸、风格、数量等参数' },
      { name: 'edit_image', desc: '对上传图片进行编辑，支持 Prompt 驱动的局部/全局修改' },
    ],
    tags: ['#文生图', '#图生图', '#火山引擎', '#Doubao'],
    iconBg: 'linear-gradient(135deg,#f97316,#ec4899)',
    iconChar: '图',
  },
  video_generation: {
    fullDesc:
      '基于火山引擎 Seedance 模型生成视频。支持纯文字描述生成视频（文生视频）以及基于图片生成对应动态视频（图生视频）。生成结果保存到本地 storage/videos/ 目录。',
    whenToUse:
      '当用户说「生成一段视频…」「把这张图做成动态的…」「制作一个…的短视频」时触发。',
    tools: [
      { name: 'generate_video', desc: '文字描述 → 视频，支持时长、分辨率参数' },
      { name: 'image_to_video', desc: '输入参考图 + 描述 → 动态视频' },
    ],
    tags: ['#文生视频', '#图生视频', '#Seedance'],
    iconBg: 'linear-gradient(135deg,#8b5cf6,#3b82f6)',
    iconChar: '视',
  },
  video_concatenation: {
    fullDesc:
      '将多个视频片段按照分镜顺序拼接为一条完整长视频。支持自定义每段时长、添加转场、配合 BGM 混音输出最终成片。',
    whenToUse: '当用户需要「把这几段视频合并」「制作分镜视频」「视频剪辑拼接」时触发。',
    tools: [
      { name: 'concatenate_videos', desc: '按顺序拼接多个视频片段' },
      { name: 'add_bgm', desc: '为拼接结果添加背景音乐' },
    ],
    tags: ['#视频拼接', '#分镜', '#长视频'],
    iconBg: 'linear-gradient(135deg,#0ea5e9,#6366f1)',
    iconChar: '拼',
  },
  model_3d: {
    fullDesc:
      '基于腾讯混元 AI3D 模型，从文字描述或参考图片生成三维模型（OBJ/GLB 格式）。生成的模型可以在画布上实时预览，支持旋转和缩放查看。',
    whenToUse: '当用户说「生成一个 3D 模型…」「把这张图转成 3D…」「做一个三维的…」时触发。',
    tools: [
      { name: 'generate_3d_model', desc: '文字/图片 → 3D 模型（OBJ/GLB）' },
    ],
    tags: ['#3D建模', '#腾讯混元', '#OBJ', '#GLB'],
    iconBg: 'linear-gradient(135deg,#10b981,#06b6d4)',
    iconChar: '3D',
  },
  virtual_anchor: {
    fullDesc:
      '通过 ComfyUI 工作流驱动口型同步，将真实人脸视频或静态图片与音频结合，生成开口说话的虚拟形象视频。',
    whenToUse: '当用户说「做一个虚拟主播」「让这张图开口说话」「生成口型同步视频」时触发。',
    tools: [
      { name: 'generate_virtual_anchor', desc: '人脸图/视频 + 音频 → 口型同步视频' },
    ],
    tags: ['#虚拟人', '#口型同步', '#ComfyUI', '#数字人'],
    iconBg: 'linear-gradient(135deg,#f59e0b,#ef4444)',
    iconChar: '人',
  },
  tts: {
    fullDesc:
      '基于阿里云百炼 CosyVoice 模型，提供音色设计与克隆功能。可以选择预置音色或上传参考音频克隆特定嗓音，输出高质量语音文件。',
    whenToUse: '当用户说「朗读这段文字」「生成语音」「用某个声音念…」「做一段配音」时触发。',
    tools: [
      { name: 'text_to_speech', desc: '文字 → 语音，支持多种预置音色' },
      { name: 'clone_voice', desc: '上传参考音频克隆特定音色' },
    ],
    tags: ['#语音合成', '#CosyVoice', '#音色克隆', '#TTS'],
    iconBg: 'linear-gradient(135deg,#14b8a6,#3b82f6)',
    iconChar: '音',
  },
  audio_mixing: {
    fullDesc:
      '对多段音频文件进行拼接、音量调整、添加 BGM、混音输出。适合为生成的视频配乐或制作播客等音频内容。',
    whenToUse: '当用户说「给这段视频加背景音乐」「混音」「把这几段音频合并」时触发。',
    tools: [
      { name: 'mix_audio', desc: '多轨音频混音，支持音量比例调整' },
      { name: 'concat_audio', desc: '将多段音频顺序拼接' },
    ],
    tags: ['#音频混音', '#BGM', '#音频拼接'],
    iconBg: 'linear-gradient(135deg,#a855f7,#ec4899)',
    iconChar: '混',
  },
  xiaohongshu_post: {
    fullDesc:
      '专为小红书平台设计的图文帖子生成工具。能够根据主题或产品描述，自动生成符合小红书风格的标题（含 emoji）、正文、话题标签，并可选配图方案建议。',
    whenToUse:
      '当用户说「帮我写一篇小红书」「生成小红书文案」「写一个种草帖」「做一个小红书图文帖子」时触发。',
    tools: [
      { name: 'write_xhs_post', desc: '生成小红书风格的标题 + 正文 + 话题标签' },
      { name: 'suggest_cover_style', desc: '根据内容建议封面图风格和构图方向' },
    ],
    tags: ['#小红书', '#图文帖', '#种草', '#内容创作', '#文案'],
    iconBg: 'linear-gradient(135deg,#ff2442,#ff6b6b)',
    iconChar: '书',
  },
}

// ─── Helper ──────────────────────────────────────────────────────────
function goHome() {
  const url = new URL(window.location.href)
  url.searchParams.delete('page')
  window.history.pushState({}, '', url.toString())
  window.dispatchEvent(new PopStateEvent('popstate'))
}

const CATEGORY_LABELS: Record<string, string> = {
  image: '图片',
  video: '视频',
  '3d': '3D',
  avatar: '虚拟人',
  audio: '音频',
  content: '内容创作',
}

const CATEGORY_ORDER = ['image', 'video', '3d', 'avatar', 'audio', 'content']

// ─── Tool Detail Panel ────────────────────────────────────────────────
function ToolDetailPanel({ tool }: { tool: ToolItem }) {
  const detail = TOOL_DETAILS[tool.id]

  return (
    <div className="skill-detail-panel">
      <div className="skill-detail-panel__header">
        <div
          className="skill-detail-panel__icon"
          style={{ background: detail?.iconBg || 'linear-gradient(135deg,#64748b,#475569)' }}
        >
          {detail?.iconChar ?? tool.name.charAt(0)}
        </div>
        <div className="skill-detail-panel__meta">
          <div className="skill-detail-panel__name">{tool.name}</div>
          <span className="skill-detail-panel__cat-badge">
            {CATEGORY_LABELS[tool.category] || tool.category}
          </span>
        </div>
      </div>

      <div className="skill-detail-panel__source">
        <span className="skill-detail-panel__source-label">来源</span>
        <span className="skill-detail-panel__source-val">FishStudio 内置</span>
      </div>

      <p className="skill-detail-panel__full-desc">
        {detail?.fullDesc ?? tool.description}
      </p>

      {detail?.whenToUse && (
        <div className="skill-detail-panel__section">
          <div className="skill-detail-panel__section-title">
            <Zap size={13} />
            触发时机
          </div>
          <p className="skill-detail-panel__section-body">{detail.whenToUse}</p>
        </div>
      )}

      {detail?.tools && detail.tools.length > 0 && (
        <div className="skill-detail-panel__section">
          <div className="skill-detail-panel__section-title">
            <Wrench size={13} />
            包含工具
            <span className="skill-detail-panel__section-badge">{detail.tools.length}</span>
          </div>
          <div className="skill-detail-panel__tools">
            {detail.tools.map((t) => (
              <div className="skill-detail-panel__tool-card" key={t.name}>
                <div className="skill-detail-panel__tool-name">{t.name}</div>
                <div className="skill-detail-panel__tool-desc">{t.desc}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {detail?.tags && detail.tags.length > 0 && (
        <div className="skill-detail-panel__section">
          <div className="skill-detail-panel__section-title">
            <Tag size={13} />
            标签
          </div>
          <div className="skill-detail-panel__tags">
            {detail.tags.map((t) => (
              <span className="skill-detail-panel__tag" key={t}>{t}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── ToolsPanel ───────────────────────────────────────────────────────
function ToolsPanel() {
  const [tools, setTools] = useState<ToolItem[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch('/api/settings/skills')
      .then((r) => r.json())
      .then((d) => setTools(d.skills || []))
      .catch(() => setToast({ type: 'error', msg: '加载失败' }))
      .finally(() => setLoading(false))
  }, [])

  const toggle = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setTools((prev) => prev.map((s) => (s.id === id ? { ...s, enabled: !s.enabled } : s)))
  }

  const save = async () => {
    setSaving(true)
    setToast(null)
    try {
      const res = await fetch('/api/settings/skills', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skills: tools }),
      })
      if (!res.ok) throw new Error()
      setToast({ type: 'success', msg: '已保存' })
      setTimeout(() => setToast(null), 2500)
    } catch {
      setToast({ type: 'error', msg: '保存失败，请重试' })
    } finally {
      setSaving(false)
    }
  }

  const grouped = tools.reduce<Record<string, ToolItem[]>>((acc, s) => {
    const cat = s.category || 'other'
    ;(acc[cat] = acc[cat] || []).push(s)
    return acc
  }, {})
  const orderedCats = [
    ...CATEGORY_ORDER.filter((c) => grouped[c]),
    ...Object.keys(grouped).filter((c) => !CATEGORY_ORDER.includes(c)),
  ]

  const selectedTool = tools.find((s) => s.id === selectedId) ?? null

  if (loading) return <div className="settings-loading">加载中…</div>

  return (
    <div className={`skills-layout${selectedTool ? ' skills-layout--split' : ''}`}>
      <div className="skills-list">
        <div className="settings-panel__title">工具配置</div>
        <div className="settings-panel__desc">
          控制各模态工具是否在会话中启用。点击卡片查看详情，右侧开关可快速启用/关闭。
        </div>

        {orderedCats.map((cat) => (
          <div key={cat}>
            <div className="settings-group-title">{CATEGORY_LABELS[cat] || cat}</div>
            {grouped[cat].map((tool) => {
              const detail = TOOL_DETAILS[tool.id]
              const isSelected = selectedId === tool.id
              return (
                <div
                  className={`skill-card${isSelected ? ' skill-card--active' : ''}`}
                  key={tool.id}
                  onClick={() => setSelectedId(isSelected ? null : tool.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && setSelectedId(isSelected ? null : tool.id)}
                >
                  <div
                    className="skill-card__icon"
                    style={{
                      background: detail?.iconBg || 'linear-gradient(135deg,#64748b,#475569)',
                    }}
                  >
                    {detail?.iconChar ?? tool.name.charAt(0)}
                  </div>
                  <div className="skill-card__info">
                    <div className="skill-card__name">{tool.name}</div>
                    <div className="skill-card__desc">{tool.description}</div>
                  </div>
                  <label className="toggle-switch" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={tool.enabled}
                      onChange={(e) => { e.stopPropagation(); toggle(tool.id, e as any) }}
                    />
                    <span className="toggle-switch__track" />
                  </label>
                </div>
              )
            })}
          </div>
        ))}

        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
          <button className="settings-save-btn" onClick={save} disabled={saving}>
            {saving ? '保存中…' : <><Save size={15} />保存</>}
          </button>
          {toast && (
            <span className={`settings-toast settings-toast--${toast.type}`}>
              {toast.type === 'success' ? <Check size={14} /> : <AlertCircle size={14} />}
              {toast.msg}
            </span>
          )}
        </div>
      </div>

      {selectedTool && <ToolDetailPanel tool={selectedTool} />}
    </div>
  )
}

// ─── Skill File Preview Panel ─────────────────────────────────────────
function SkillFilePreview({ skill }: { skill: InstalledSkill }) {
  const [content, setContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    setLoading(true)
    setError(false)
    fetch(`/api/settings/skills/installed/${skill.id}/content`)
      .then((r) => {
        if (!r.ok) throw new Error()
        return r.json()
      })
      .then((d) => setContent(d.content))
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [skill.id])

  return (
    <div className="skill-file-preview">
      {/* Header */}
      <div className="skill-file-preview__header">
        <div className="skill-file-preview__title">
          <Package size={14} />
          {skill.name}
        </div>
        <span
          className="installed-skill-badge"
          style={{
            background: skill.source === 'custom'
              ? 'linear-gradient(135deg,#7c3aed,#a855f7)'
              : 'linear-gradient(135deg,#2563eb,#3b82f6)',
          }}
        >
          {skill.source}
        </span>
      </div>

      <div className="skill-file-preview__filename">SKILL.md</div>

      {/* Content */}
      <div className="skill-file-preview__body">
        {loading && <div className="skill-file-preview__loading">加载中…</div>}
        {error && <div className="skill-file-preview__error">读取文件失败</div>}
        {!loading && !error && content !== null && (
          <div className="skill-file-preview__markdown">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── WorkspacePanel ───────────────────────────────────────────────────
interface WorkspaceFile {
  id: string
  name: string
  desc: string
  content: string
}

const WORKSPACE_FILE_ICONS: Record<string, { emoji: string; bg: string }> = {
  'AGENTS.md':   { emoji: '📋', bg: 'linear-gradient(135deg,#ef4444,#f97316)' },
  'TOOLS.md':    { emoji: '🔧', bg: 'linear-gradient(135deg,#8b5cf6,#6366f1)' },
  'IDENTITY.md': { emoji: '🎭', bg: 'linear-gradient(135deg,#ec4899,#f43f5e)' },
  'USER.md':     { emoji: '🧑', bg: 'linear-gradient(135deg,#3b82f6,#6366f1)' },
  'SOUL.md':     { emoji: '✨', bg: 'linear-gradient(135deg,#f59e0b,#f97316)' },
  'MEMORY.md':   { emoji: '💾', bg: 'linear-gradient(135deg,#10b981,#06b6d4)' },
}

function WorkspacePanel() {
  const [files, setFiles] = useState<WorkspaceFile[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [editContent, setEditContent] = useState('')
  const [previewMode, setPreviewMode] = useState(false)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch('/api/settings/workspace')
      .then((r) => r.json())
      .then((data: WorkspaceFile[]) => {
        setFiles(Array.isArray(data) ? data : [])
        if (data.length > 0) {
          setSelectedId(data[0].id)
          setEditContent(data[0].content)
        }
      })
      .catch(() => setToast({ type: 'error', msg: '加载失败' }))
      .finally(() => setLoading(false))
  }, [])

  const handleSelect = (file: WorkspaceFile) => {
    setSelectedId(file.id)
    setEditContent(file.content)
    setPreviewMode(false)
    setToast(null)
  }

  const handleSave = async () => {
    if (!selectedId) return
    setSaving(true)
    setToast(null)
    try {
      const res = await fetch(`/api/settings/workspace/${selectedId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: editContent }),
      })
      if (!res.ok) throw new Error()
      setFiles((prev) => prev.map((f) => f.id === selectedId ? { ...f, content: editContent } : f))
      setToast({ type: 'success', msg: '已保存' })
      setTimeout(() => setToast(null), 2500)
    } catch {
      setToast({ type: 'error', msg: '保存失败，请重试' })
    } finally {
      setSaving(false)
    }
  }

  const selectedFile = files.find((f) => f.id === selectedId) ?? null

  if (loading) return <div className="settings-loading">加载中…</div>

  return (
    <div className="workspace-layout">
      {/* ── 左：文件列表 ── */}
      <div className="workspace-list">
        <div className="settings-panel__title">工作空间</div>
        <div className="settings-panel__desc">
          Agent 每次对话会自动读取这些文件，作为身份记忆注入 System Prompt。
        </div>

        {toast && (
          <span className={`settings-toast settings-toast--${toast.type}`} style={{ marginBottom: 10, display: 'flex' }}>
            {toast.type === 'success' ? <Check size={14} /> : <AlertCircle size={14} />}
            {toast.msg}
          </span>
        )}

        {files.map((file) => {
          const meta = WORKSPACE_FILE_ICONS[file.id] ?? { emoji: '📄', bg: 'linear-gradient(135deg,#64748b,#475569)' }
          const isSelected = selectedId === file.id
          const charCount = file.content.trim().length
          return (
            <div
              key={file.id}
              className={`workspace-card${isSelected ? ' workspace-card--active' : ''}`}
              onClick={() => handleSelect(file)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === 'Enter' && handleSelect(file)}
            >
              <div className="workspace-card__icon" style={{ background: meta.bg }}>
                {meta.emoji}
              </div>
              <div className="workspace-card__info">
                <div className="workspace-card__name">{file.name}</div>
                <div className="workspace-card__filename">{file.id}</div>
                <div className="workspace-card__chars">
                  {charCount > 0 ? `${charCount} 字符` : '（空）'}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* ── 右：编辑区 ── */}
      {selectedFile && (
        <div className="workspace-editor">
          <div className="workspace-editor__header">
            <div className="workspace-editor__title">
              <Brain size={15} />
              {selectedFile.name}
            </div>
            <div className="workspace-editor__desc">{selectedFile.desc}</div>
          </div>

          <div className="workspace-editor__toolbar">
            <button
              className={`workspace-editor__mode-btn${!previewMode ? ' workspace-editor__mode-btn--active' : ''}`}
              onClick={() => setPreviewMode(false)}
            >
              编辑
            </button>
            <button
              className={`workspace-editor__mode-btn${previewMode ? ' workspace-editor__mode-btn--active' : ''}`}
              onClick={() => setPreviewMode(true)}
            >
              <Eye size={13} />
              预览
            </button>
          </div>

          {previewMode ? (
            <div className="workspace-editor__preview">
              <ReactMarkdown>{editContent}</ReactMarkdown>
            </div>
          ) : (
            <textarea
              className="workspace-editor__textarea"
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              placeholder={`在此编辑 ${selectedFile.id}…`}
              spellCheck={false}
            />
          )}

          <div className="workspace-editor__footer">
            <button className="settings-save-btn" onClick={handleSave} disabled={saving}>
              {saving ? '保存中…' : <><Save size={15} />保存</>}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── SkillsPanel ──────────────────────────────────────────────────────
function SkillsPanel() {
  const [skills, setSkills] = useState<InstalledSkill[]>([])
  const [loading, setLoading] = useState(true)
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch('/api/settings/skills/installed')
      .then((r) => r.json())
      .then((data) => setSkills(Array.isArray(data) ? data : []))
      .catch(() => setToast({ type: 'error', msg: '加载失败' }))
      .finally(() => setLoading(false))
  }, [])

  const handleToggle = async (skill: InstalledSkill, e: React.MouseEvent) => {
    e.stopPropagation()
    if (togglingId === skill.id) return
    const newEnabled = !skill.enabled
    setSkills((prev) => prev.map((s) => s.id === skill.id ? { ...s, enabled: newEnabled } : s))
    setTogglingId(skill.id)
    try {
      const res = await fetch(`/api/settings/skills/installed/${skill.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: newEnabled }),
      })
      if (!res.ok) throw new Error()
    } catch {
      setSkills((prev) => prev.map((s) => s.id === skill.id ? { ...s, enabled: skill.enabled } : s))
      setToast({ type: 'error', msg: '操作失败，请重试' })
      setTimeout(() => setToast(null), 2500)
    } finally {
      setTogglingId(null)
    }
  }

  const selectedSkill = skills.find((s) => s.id === selectedId) ?? null

  if (loading) return <div className="settings-loading">加载中…</div>

  return (
    <div className={`skills-layout${selectedSkill ? ' skills-layout--split' : ''}`}>
      {/* ── 左：skill 列表 ── */}
      <div className="skills-list">
        <div className="settings-panel__title">Skills</div>
        <div className="settings-panel__desc">
          已安装的 Skill 文件（SKILL.md）。启用后，Agent 对话时会自动读取对应的领域知识。点击卡片可预览文件内容。
        </div>

        {toast && (
          <span className={`settings-toast settings-toast--${toast.type}`} style={{ marginBottom: 10, display: 'flex' }}>
            {toast.type === 'success' ? <Check size={14} /> : <AlertCircle size={14} />}
            {toast.msg}
          </span>
        )}

        {skills.length === 0 ? (
          <div className="skills-empty">
            <Package size={32} style={{ opacity: 0.3 }} />
            <div>暂无已安装的 Skills</div>
            <div style={{ fontSize: 12, opacity: 0.45 }}>
              将 SKILL.md 放入 <code>skills/custom/</code> 目录即可
            </div>
          </div>
        ) : (
          skills.map((skill) => {
            const isSelected = selectedId === skill.id
            return (
              <div
                className={`skill-card${isSelected ? ' skill-card--active' : ''}`}
                key={skill.id}
                onClick={() => setSelectedId(isSelected ? null : skill.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && setSelectedId(isSelected ? null : skill.id)}
              >
                {/* 左：信息 */}
                <div className="skill-card__info" style={{ flex: 1, minWidth: 0 }}>
                  <div className="skill-card__name" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    {skill.name}
                    <span
                      className="installed-skill-badge"
                      style={{
                        background: skill.source === 'custom'
                          ? 'linear-gradient(135deg,#7c3aed,#a855f7)'
                          : 'linear-gradient(135deg,#2563eb,#3b82f6)',
                      }}
                    >
                      {skill.source}
                    </span>
                  </div>
                  <div
                    className="skill-card__desc"
                    style={{
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                    }}
                  >
                    {skill.description}
                  </div>
                  <div style={{ fontSize: 11, opacity: 0.4, fontFamily: 'monospace', marginTop: 2 }}>
                    {skill.id}
                  </div>
                </div>
                {/* 右：toggle */}
                <label
                  className="toggle-switch"
                  onClick={(e) => handleToggle(skill, e)}
                >
                  <input
                    type="checkbox"
                    checked={skill.enabled}
                    onChange={() => {}}
                    disabled={togglingId === skill.id}
                  />
                  <span className="toggle-switch__track" />
                </label>
              </div>
            )
          })
        )}
      </div>

      {/* ── 右：SKILL.md 预览 ── */}
      {selectedSkill && <SkillFilePreview skill={selectedSkill} />}
    </div>
  )
}

// ─── MCPPanel ─────────────────────────────────────────────────────────
function toFormList(mcpServers: Record<string, any>): MCPServerForm[] {
  return Object.entries(mcpServers).map(([name, cfg]) => ({
    _key: `${name}-${Date.now()}-${Math.random()}`,
    name,
    command: cfg.command || '',
    args: Array.isArray(cfg.args) ? cfg.args.join(', ') : '',
    envPairs: cfg.env
      ? Object.entries(cfg.env as Record<string, string>).map(([k, v]) => ({ k, v }))
      : [],
  }))
}

function fromFormList(forms: MCPServerForm[]): Record<string, any> {
  const result: Record<string, any> = {}
  for (const f of forms) {
    const name = f.name.trim()
    if (!name) continue
    const server: any = {}
    if (f.command.trim()) server.command = f.command.trim()
    const args = f.args.split(',').map((a) => a.trim()).filter(Boolean)
    if (args.length) server.args = args
    if (f.envPairs.length) {
      server.env = Object.fromEntries(
        f.envPairs.filter((p) => p.k.trim()).map((p) => [p.k.trim(), p.v])
      )
    }
    result[name] = server
  }
  return result
}

function MCPPanel() {
  const [view, setView] = useState<MCPView>('form')
  const [forms, setForms] = useState<MCPServerForm[]>([])
  const [jsonText, setJsonText] = useState('{}')
  const [jsonError, setJsonError] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch('/api/settings/mcp')
      .then((r) => r.json())
      .then((d) => {
        const servers = d.mcpServers || {}
        setForms(toFormList(servers))
        setJsonText(JSON.stringify({ mcpServers: servers }, null, 2))
      })
      .catch(() => setToast({ type: 'error', msg: '加载失败' }))
      .finally(() => setLoading(false))
  }, [])

  const switchToJson = () => {
    setJsonText(JSON.stringify({ mcpServers: fromFormList(forms) }, null, 2))
    setJsonError('')
    setView('json')
  }

  const switchToForm = () => {
    try {
      const parsed = JSON.parse(jsonText)
      setForms(toFormList(parsed.mcpServers || {}))
      setJsonError('')
      setView('form')
    } catch {
      setJsonError('JSON 格式错误，请先修正后再切换')
    }
  }

  const handleJsonChange = (val: string) => {
    setJsonText(val)
    try { JSON.parse(val); setJsonError('') } catch { setJsonError('JSON 格式错误') }
  }

  const addServer = () => {
    setForms((prev) => [...prev, { _key: `new-${Date.now()}`, name: '', command: '', args: '', envPairs: [] }])
  }
  const removeServer = (key: string) => setForms((prev) => prev.filter((f) => f._key !== key))
  const updateForm = (key: string, patch: Partial<MCPServerForm>) =>
    setForms((prev) => prev.map((f) => (f._key === key ? { ...f, ...patch } : f)))
  const addEnvPair = (serverKey: string) =>
    setForms((prev) => prev.map((f) => f._key === serverKey ? { ...f, envPairs: [...f.envPairs, { k: '', v: '' }] } : f))
  const removeEnvPair = (serverKey: string, idx: number) =>
    setForms((prev) => prev.map((f) => f._key === serverKey ? { ...f, envPairs: f.envPairs.filter((_, i) => i !== idx) } : f))
  const updateEnvPair = (serverKey: string, idx: number, patch: { k?: string; v?: string }) =>
    setForms((prev) => prev.map((f) => f._key === serverKey
      ? { ...f, envPairs: f.envPairs.map((ep, i) => i === idx ? { ...ep, ...patch } : ep) }
      : f))

  const save = async () => {
    setSaving(true); setToast(null)
    let payload: Record<string, any>
    if (view === 'json') {
      try { payload = JSON.parse(jsonText) }
      catch { setToast({ type: 'error', msg: 'JSON 格式错误，无法保存' }); setSaving(false); return }
    } else { payload = { mcpServers: fromFormList(forms) } }
    try {
      const res = await fetch('/api/settings/mcp', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (!res.ok) throw new Error()
      setToast({ type: 'success', msg: '已保存' })
      setTimeout(() => setToast(null), 2500)
    } catch { setToast({ type: 'error', msg: '保存失败，请重试' }) }
    finally { setSaving(false) }
  }

  if (loading) return <div className="settings-loading">加载中…</div>

  return (
    <div>
      <div className="settings-panel__title">MCP 服务器配置</div>
      <div className="settings-panel__desc">配置 Model Context Protocol 服务器。支持表单编辑或直接修改原始 JSON。</div>

      <div className="mcp-view-toggle">
        <button className={`mcp-view-toggle__btn${view === 'form' ? ' mcp-view-toggle__btn--active' : ''}`} onClick={view === 'json' ? switchToForm : undefined}>表单视图</button>
        <button className={`mcp-view-toggle__btn${view === 'json' ? ' mcp-view-toggle__btn--active' : ''}`} onClick={view === 'form' ? switchToJson : undefined}>JSON 视图</button>
      </div>

      {jsonError && view === 'form' && <div className="settings-toast settings-toast--error" style={{ marginBottom: 12 }}><AlertCircle size={14} />{jsonError}</div>}

      {view === 'form' ? (
        <>
          {forms.length === 0 && <div style={{ color: 'rgba(229,231,235,0.45)', fontSize: 14, marginBottom: 12 }}>尚未配置任何 MCP 服务器</div>}
          {forms.map((f) => (
            <div className="mcp-server-row" key={f._key}>
              <div className="mcp-server-row__header">
                <span className="mcp-server-row__name-label">服务器配置</span>
                <button className="mcp-server-row__delete-btn" onClick={() => removeServer(f._key)} title="删除"><Trash2 size={14} /></button>
              </div>
              <div className="mcp-fields">
                <div className="mcp-field">
                  <label>名称 (name)</label>
                  <input className="settings-input" placeholder="my-server" value={f.name} onChange={(e) => updateForm(f._key, { name: e.target.value })} />
                </div>
                <div className="mcp-field">
                  <label>命令 (command)</label>
                  <input className="settings-input" placeholder="npx" value={f.command} onChange={(e) => updateForm(f._key, { command: e.target.value })} />
                </div>
                <div className="mcp-field mcp-field--full">
                  <label>参数 (args，逗号分隔)</label>
                  <input className="settings-input" placeholder="-y, @modelcontextprotocol/server-filesystem, /path" value={f.args} onChange={(e) => updateForm(f._key, { args: e.target.value })} />
                </div>
                <div className="mcp-field mcp-field--full">
                  <label>环境变量 (env)</label>
                  <div className="mcp-env-pairs">
                    {f.envPairs.map((ep, idx) => (
                      <div className="mcp-env-pair" key={idx}>
                        <input className="settings-input" placeholder="KEY" value={ep.k} onChange={(e) => updateEnvPair(f._key, idx, { k: e.target.value })} style={{ maxWidth: 160 }} />
                        <input className="settings-input" placeholder="VALUE" value={ep.v} onChange={(e) => updateEnvPair(f._key, idx, { v: e.target.value })} />
                        <button className="mcp-env-pair__remove" onClick={() => removeEnvPair(f._key, idx)} title="删除"><Trash2 size={12} /></button>
                      </div>
                    ))}
                    <button className="mcp-add-env-btn" onClick={() => addEnvPair(f._key)}><Plus size={12} />添加环境变量</button>
                  </div>
                </div>
              </div>
            </div>
          ))}
          <button className="mcp-add-server-btn" onClick={addServer}><Plus size={16} />添加服务器</button>
        </>
      ) : (
        <>
          <textarea className={`mcp-json-editor${jsonError ? ' mcp-json-editor--error' : ''}`} value={jsonText} onChange={(e) => handleJsonChange(e.target.value)} spellCheck={false} />
          {jsonError && <div className="settings-toast settings-toast--error" style={{ marginTop: 6 }}><AlertCircle size={14} />{jsonError}</div>}
        </>
      )}

      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <button className="settings-save-btn" onClick={save} disabled={saving || (view === 'json' && !!jsonError)}>
          {saving ? '保存中…' : <><Save size={15} />保存</>}
        </button>
        {toast && <span className={`settings-toast settings-toast--${toast.type}`}>{toast.type === 'success' ? <Check size={14} /> : <AlertCircle size={14} />}{toast.msg}</span>}
      </div>
    </div>
  )
}

// ─── EnvPanel ─────────────────────────────────────────────────────────
function EnvPanel() {
  const [groups, setGroups] = useState<EnvGroup[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [revealedKeys, setRevealedKeys] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch('/api/settings/env')
      .then((r) => r.json())
      .then((d) => {
        const grps: EnvGroup[] = d.groups || []
        setGroups(grps)
        const init: Record<string, string> = {}
        for (const g of grps) for (const item of g.items) init[item.key] = item.value
        setValues(init)
      })
      .catch(() => setToast({ type: 'error', msg: '加载失败' }))
      .finally(() => setLoading(false))
  }, [])

  const toggleReveal = (key: string) =>
    setRevealedKeys((prev) => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n })

  const save = async () => {
    setSaving(true); setToast(null)
    try {
      const res = await fetch('/api/settings/env', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: Object.entries(values).map(([key, value]) => ({ key, value })) }),
      })
      if (!res.ok) throw new Error()
      setToast({ type: 'success', msg: '已保存，部分配置需重启后端生效' })
      setTimeout(() => setToast(null), 4000)
    } catch { setToast({ type: 'error', msg: '保存失败，请重试' }) }
    finally { setSaving(false) }
  }

  if (loading) return <div className="settings-loading">加载中…</div>

  return (
    <div>
      <div className="settings-panel__title">系统环境变量</div>
      <div className="settings-panel__desc">直接读写 backend/.env 文件中的配置项。API Key 类字段默认隐藏，点击眼睛图标可查看原始值。修改后需重启后端服务才能完全生效。</div>
      {groups.map((g) => (
        <div className="env-group" key={g.group}>
          <div className="env-group__title">{g.group}</div>
          {g.items.map((item) => {
            const revealed = revealedKeys.has(item.key)
            return (
              <div className="env-row" key={item.key}>
                <div className="env-row__key-col">
                  <div className="env-row__key">{item.key}</div>
                  <div className="env-row__desc">{item.desc}</div>
                </div>
                <div className="env-row__value-col">
                  <input className="settings-input" type={item.sensitive && !revealed ? 'password' : 'text'} value={values[item.key] ?? ''} onChange={(e) => setValues((v) => ({ ...v, [item.key]: e.target.value }))} placeholder="（未设置）" />
                  {item.sensitive && (
                    <button className="env-row__eye-btn" onClick={() => toggleReveal(item.key)} title={revealed ? '隐藏' : '查看'}>
                      {revealed ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ))}
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <button className="settings-save-btn" onClick={save} disabled={saving}>
          {saving ? '保存中…' : <><Save size={15} />保存</>}
        </button>
        {toast && <span className={`settings-toast settings-toast--${toast.type}`}>{toast.type === 'success' ? <Check size={14} /> : <AlertCircle size={14} />}{toast.msg}</span>}
      </div>
    </div>
  )
}

// ─── SettingsPage ─────────────────────────────────────────────────────
interface SettingsPageProps {
  theme: ThemeMode
  onToggleTheme: () => void
}

const TABS: { id: TabId; label: string; icon: React.ReactNode; badge?: string }[] = [
  { id: 'workspace', label: '工作空间', icon: <Brain size={16} />, badge: 'NEW' },
  { id: 'skills', label: 'Skills', icon: <Package size={16} /> },
  { id: 'tools', label: '工具', icon: <Wrench size={16} /> },
  { id: 'mcp', label: 'MCP 服务器', icon: <Server size={16} /> },
  { id: 'env', label: '环境变量', icon: <FileCode2 size={16} /> },
]

const NEW_BADGE_KEY = 'fishstudio_workspace_seen'

export default function SettingsPage({ theme, onToggleTheme }: SettingsPageProps) {
  const [activeTab, setActiveTab] = useState<TabId>('workspace')
  const [workspaceSeen, setWorkspaceSeen] = useState<boolean>(
    () => !!localStorage.getItem(NEW_BADGE_KEY)
  )

  const handleTabClick = (id: TabId) => {
    setActiveTab(id)
    if (id === 'workspace' && !workspaceSeen) {
      localStorage.setItem(NEW_BADGE_KEY, '1')
      setWorkspaceSeen(true)
    }
  }

  const renderPanel = useCallback(() => {
    switch (activeTab) {
      case 'workspace': return <WorkspacePanel />
      case 'skills': return <SkillsPanel />
      case 'tools':  return <ToolsPanel />
      case 'mcp':    return <MCPPanel />
      case 'env':    return <EnvPanel />
    }
  }, [activeTab])

  return (
    <div className="settings-page">
      <div className="settings-page__bg" />
      <header className="settings-page__header">
        <div className="settings-page__header-left">
          <button className="settings-page__back-btn" onClick={goHome}>
            <ArrowLeft size={16} />返回
          </button>
          <span className="settings-page__title">设置</span>
        </div>
        <div className="settings-page__header-right">
          <button className="settings-page__theme-btn" onClick={onToggleTheme}>
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            {theme === 'dark' ? '亮色' : '暗色'}
          </button>
        </div>
      </header>
      <div className="settings-page__body">
        <nav className="settings-page__sidebar">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              className={`settings-page__tab${activeTab === tab.id ? ' settings-page__tab--active' : ''}`}
              onClick={() => handleTabClick(tab.id)}
            >
              {tab.icon}{tab.label}
              {tab.badge && !workspaceSeen && <span className="settings-page__tab-badge">{tab.badge}</span>}
            </button>
          ))}
        </nav>
        <main className="settings-page__content">{renderPanel()}</main>
      </div>
    </div>
  )
}
