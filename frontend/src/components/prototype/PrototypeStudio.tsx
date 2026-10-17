import { useMemo, useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  HelpCircle,
  Clock3,
  Film,
  FolderKanban,
  Home,
  Image,
  Mic2,
  Shapes,
  Layers3,
  Menu,
  MessageSquare,
  MoreHorizontal,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  UserRound,
  Wand2,
  X,
} from 'lucide-react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import './PrototypeStudio.css'

const prototypePaths = {
  home: '/prototype',
  projects: '/prototype/projects',
  newProject: '/prototype/projects/new',
  dramaImport: '/prototype/projects/new/drama-import',
  dramaParse: '/prototype/projects/new/drama-parse',
  characters: '/prototype/projects/midnight-convenience/characters',
  freeWorkspace: '/prototype/projects/product-poster/workspace',
  storyboard: '/prototype/projects/midnight-convenience/storyboard',
}

const shots = [
  { id: 'SH01', label: '便利店外景', status: '已采用', tone: 'approved' },
  { id: 'SH02', label: '林夏推门', status: '待审核', tone: 'review' },
  { id: 'SH03', label: '顾远抬头', status: '待审核', tone: 'review' },
  { id: 'SH04', label: '货架对视', status: '生成中', tone: 'running' },
  { id: 'SH05', label: '袖口特写', status: '需修复', tone: 'failed' },
  { id: 'SH06', label: '雨夜街口', status: '可生成', tone: 'ready' },
  { id: 'SH07', label: '收银台对白', status: '可生成', tone: 'ready' },
  { id: 'SH08', label: '背影离开', status: '未准备', tone: 'draft' },
]

const navItems = [
  { to: prototypePaths.home, label: '首页', icon: Home, end: true },
  { to: prototypePaths.projects, label: '项目', icon: FolderKanban },
  { to: '/prototype/assets', label: '素材库', icon: Image },
  { to: '/prototype/jobs', label: '生成任务', icon: Layers3, badge: '3' },
]

export function PrototypeShell() {
  const location = useLocation()
  const [copilotOpen, setCopilotOpen] = useState(false)
  const workspace = location.pathname.includes('/storyboard') || location.pathname.includes('/workspace')

  return (
    <div className="prototype">
      <aside className="prototype__sidebar">
        <Link className="prototype__brand" to={prototypePaths.home}>
          <span className="prototype__brand-mark">F</span>
          <span>FishStudio</span>
        </Link>

        <nav className="prototype__nav">
          {navItems.map(({ to, label, icon: Icon, badge, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => `prototype__nav-item${isActive ? ' is-active' : ''}`}
            >
              <Icon size={18} />
              <span>{label}</span>
              {badge && <b>{badge}</b>}
            </NavLink>
          ))}
        </nav>

        <NavLink className="prototype__nav-item prototype__settings" to="/prototype/settings">
          <Settings size={18} />
          <span>设置</span>
        </NavLink>
      </aside>

      <section className="prototype__main">
        <header className="prototype__topbar">
          <div className="prototype__crumbs">
            {location.pathname.includes('/storyboard') ? (
              <>
                <Link to={prototypePaths.projects}>项目</Link>
                <ChevronRight size={14} />
                <span>午夜便利店</span>
                <ChevronRight size={14} />
                <strong>分镜</strong>
              </>
            ) : location.pathname.includes('/workspace') ? (
              <>
                <Link to={prototypePaths.projects}>项目</Link>
                <ChevronRight size={14} />
                <span>秋季新品海报</span>
                <ChevronRight size={14} />
                <strong>自由创作</strong>
              </>
            ) : (
              <strong>{location.pathname === prototypePaths.projects ? '项目' : location.pathname === prototypePaths.newProject ? '新建项目' : '首页'}</strong>
            )}
          </div>

          <div className="prototype__top-actions">
            <label className="prototype__global-search">
              <Search size={16} />
              <input aria-label="全局搜索" placeholder="搜索项目、角色、镜头…" />
              <kbd>⌘ K</kbd>
            </label>
            <button className="prototype__icon-btn" aria-label="帮助"><HelpCircle size={18} /></button>
            <button
              className={`prototype__copilot-trigger${copilotOpen ? ' is-active' : ''}`}
              onClick={() => setCopilotOpen((open) => !open)}
            >
              <Sparkles size={16} />
              Copilot
            </button>
            <div className="prototype__avatar">Y</div>
          </div>
        </header>

        <div className={`prototype__page${workspace ? ' prototype__page--workspace' : ''}`}>
          <Outlet />
        </div>
      </section>

      {copilotOpen && (
        <aside className="prototype__copilot">
          <div className="prototype__copilot-head">
            <div>
              <span><Bot size={17} /> Copilot</span>
              <small>理解当前项目与选中镜头</small>
            </div>
            <button onClick={() => setCopilotOpen(false)} aria-label="关闭 Copilot"><X size={17} /></button>
          </div>
          <div className="prototype__copilot-body">
            <div className="prototype__copilot-context">
              <small>当前上下文</small>
              <strong>午夜便利店 · 第 1 集 · 分镜审核</strong>
            </div>
            <div className="prototype__copilot-message">
              我可以帮你检查镜头一致性、调整镜头描述，或先给未生成镜头报价。写操作会先给你预览。
            </div>
            <button className="prototype__copilot-suggestion">找出服装不一致的镜头</button>
            <button className="prototype__copilot-suggestion">给剩余 3 个镜头报价</button>
          </div>
          <div className="prototype__copilot-input">
            <MessageSquare size={17} />
            <input placeholder="描述你想修改的内容…" />
            <button><ArrowRight size={16} /></button>
          </div>
        </aside>
      )}
    </div>
  )
}

export function PrototypeHome() {
  return (
    <div className="prototype-home prototype__content">
      <div className="prototype__page-heading">
        <div>
          <p>上午好，继续把故事做完。</p>
          <h1>欢迎回来</h1>
        </div>
        <Link className="prototype__primary-btn" to={prototypePaths.newProject}>
          <Plus size={17} /> 新建项目
        </Link>
      </div>

      <section>
        <div className="prototype__section-title">
          <h2>继续创作</h2>
          <span>你有 5 个镜头等待处理</span>
        </div>
        <Link className="continue-card" to={prototypePaths.storyboard}>
          <div className="continue-card__cover">
            <span>EP 01</span>
            <div className="continue-card__cover-title">午夜<br />便利店</div>
          </div>
          <div className="continue-card__body">
            <div className="continue-card__meta">午夜便利店 · 第 1 / 12 集</div>
            <h3>5 个镜头待审核</h3>
            <p>昨晚已完成第一批视频候选生成，可以从 SH02 继续审核。</p>
            <div className="continue-card__progress"><span style={{ width: '42%' }} /></div>
            <div className="continue-card__footer">
              <span>制作进度 42%</span>
              <strong>继续审核 <ArrowRight size={15} /></strong>
            </div>
          </div>
        </Link>
      </section>

      <section>
        <div className="prototype__section-title">
          <h2>进行中任务</h2>
          <Link to="/prototype/jobs">查看全部</Link>
        </div>
        <div className="job-row">
          <div className="job-row__icon"><Film size={18} /></div>
          <div className="job-row__main">
            <div><strong>第 1 集 · 视频生成</strong><span>8 / 12</span></div>
            <div className="job-row__progress"><span style={{ width: '66%' }} /></div>
          </div>
          <span className="job-row__time"><Clock3 size={14} /> 约 6 分钟</span>
          <Link to={prototypePaths.storyboard}>查看</Link>
        </div>
      </section>

      <section>
        <div className="prototype__section-title">
          <h2>最近项目</h2>
          <Link to={prototypePaths.projects}>全部项目</Link>
        </div>
        <div className="recent-grid">
          <ProjectCard title="午夜便利店" meta="漫剧 · 第 1 / 12 集" status="5 个镜头待审核" to={prototypePaths.storyboard} variant="violet" />
          <ProjectCard title="秋季新品海报" meta="自由创作 · Canvas" status="最近生成 6 个素材" to={prototypePaths.freeWorkspace} variant="blue" />
          <ProjectCard title="南风旧事" meta="漫剧 · 第 6 / 6 集" status="已完成，可交付" to={prototypePaths.projects} variant="sand" />
        </div>
      </section>
    </div>
  )
}

function ProjectCard({ title, meta, status, to, variant }: { title: string; meta: string; status: string; to: string; variant: string }) {
  return (
    <Link className="recent-card" to={to}>
      <div className={`recent-card__cover recent-card__cover--${variant}`}>
        <span>9:16</span>
        <strong>{title.slice(0, 2)}</strong>
      </div>
      <div className="recent-card__body">
        <h3>{title}</h3>
        <p>{meta}</p>
        <span>{status}</span>
      </div>
    </Link>
  )
}

export function PrototypeProjects() {
  const [filter, setFilter] = useState<'全部' | '进行中' | '待审核' | '已完成'>('全部')
  const [query, setQuery] = useState('')

  const projects = useMemo(() => [
    { title: '午夜便利店', type: '漫剧', progress: '第 1 / 12 集', status: '5 个镜头待审核', group: '待审核', variant: 'violet', updated: '12 分钟前', to: prototypePaths.storyboard },
    { title: '秋季新品海报', type: '自由创作', progress: 'Canvas · 12 个素材', status: '最近生成 6 个素材', group: '进行中', variant: 'blue', updated: '今天 10:22', to: prototypePaths.freeWorkspace },
    { title: '南风旧事', type: '漫剧', progress: '第 6 / 6 集', status: '已完成，可交付', group: '已完成', variant: 'sand', updated: '昨天', to: prototypePaths.projects },
    { title: '凌晨三点的信', type: '漫剧', progress: '第 2 / 10 集', status: '角色设定待确认', group: '进行中', variant: 'rose', updated: '9 月 20 日', to: prototypePaths.projects },
  ].filter((item) => (filter === '全部' || item.group === filter) && item.title.includes(query.trim())), [filter, query])

  return (
    <div className="prototype-projects prototype__content">
      <div className="prototype__page-heading">
        <div>
          <p>自由创作与漫剧项目统一管理</p>
          <h1>项目</h1>
        </div>
        <Link className="prototype__primary-btn" to={prototypePaths.newProject}><Plus size={17} /> 新建项目</Link>
      </div>

      <div className="project-toolbar">
        <div className="project-filters">
          {(['全部', '进行中', '待审核', '已完成'] as const).map((name) => (
            <button className={filter === name ? 'is-active' : ''} onClick={() => setFilter(name)} key={name}>{name}</button>
          ))}
        </div>
        <label className="project-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索项目" /></label>
      </div>

      <div className="project-grid">
        {projects.map((project) => (
          <Link className="project-tile" to={project.to} key={project.title}>
            <div className={`project-tile__cover recent-card__cover--${project.variant}`}>
              <span>9:16</span>
              <strong>{project.title.slice(0, 2)}</strong>
              <button aria-label="更多操作"><MoreHorizontal size={17} /></button>
            </div>
            <div className="project-tile__body">
              <div><h3>{project.title}</h3><small>{project.updated}</small></div>
              <p><b className="project-type-label">{project.type}</b>{project.progress}</p>
              <span className={`status-pill status-pill--${project.group}`}>{project.status}</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}

export function PrototypeNewProject() {
  return (
    <div className="prototype__content project-create">
      <div className="prototype__page-heading project-create__heading">
        <div>
          <p>先选择创作方式，后续仍共享同一套素材与生成能力</p>
          <h1>新建项目</h1>
        </div>
        <Link className="prototype__secondary-btn" to={prototypePaths.projects}><ArrowLeft size={15} /> 返回项目</Link>
      </div>

      <div className="project-type-grid">
        <Link className="project-type-card" to={prototypePaths.freeWorkspace}>
          <div className="project-type-card__icon"><Wand2 size={23} /></div>
          <div className="project-type-card__copy">
            <div className="project-type-card__eyebrow">FREE CREATION</div>
            <h2>自由创作</h2>
            <p>从 Canvas、对话或素材开始，自由调用图片、视频、TTS、3D、虚拟人等能力。</p>
          </div>
          <div className="project-type-card__features">
            <span><Image size={15} /> 图片 / 视频</span>
            <span><Mic2 size={15} /> TTS / 声音</span>
            <span><Shapes size={15} /> 3D / 多模态</span>
          </div>
          <strong className="project-type-card__action">进入自由工作区 <ArrowRight size={16} /></strong>
        </Link>

        <Link className="project-type-card project-type-card--drama" to={prototypePaths.dramaImport}>
          <div className="project-type-card__icon"><Film size={23} /></div>
          <div className="project-type-card__copy">
            <div className="project-type-card__eyebrow">COMIC DRAMA</div>
            <h2>漫剧项目</h2>
            <p>上传已经可生产的剧本，解析 Episode / Scene / Shot，并进入一致性、审核、剪辑与交付流程。</p>
          </div>
          <div className="project-type-card__flow">
            <span>上传剧本</span><ChevronRight size={14} />
            <span>结构确认</span><ChevronRight size={14} />
            <span>角色/场景</span><ChevronRight size={14} />
            <span>分镜生产</span>
          </div>
          <strong className="project-type-card__action">创建漫剧项目 <ArrowRight size={16} /></strong>
        </Link>
      </div>

      <div className="project-create__note">
        <Sparkles size={17} />
        <div>
          <strong>生成能力不会被项目类型限制</strong>
          <p>同一个图片、视频、TTS、3D 或虚拟人能力，既可以在自由创作中直接使用，也可以在漫剧的角色、场景、Shot 或剪辑上下文中调用。</p>
        </div>
      </div>
    </div>
  )
}

export function PrototypeDramaImport() {
  return (
    <div className="prototype__content drama-import">
      <div className="prototype__page-heading project-create__heading">
        <div>
          <p>漫剧项目 · 第 1 步</p>
          <h1>上传可生产剧本</h1>
        </div>
        <Link className="prototype__secondary-btn" to={prototypePaths.newProject}><ArrowLeft size={15} /> 返回类型选择</Link>
      </div>

      <div className="drama-import__grid">
        <Link className="drama-import-card" to={prototypePaths.dramaParse}>
          <div className="drama-import-card__badge">推荐</div>
          <div className="project-type-card__icon"><Film size={22} /></div>
          <h2>上传完整剧本</h2>
          <p>适合首次创建项目。一次导入整本、整季或完整多集剧本，系统统一解析 Episode / Scene / Shot / Character。</p>
          <div className="drama-import-card__meta">
            <span>整本 / 整季</span>
            <span>首次导入</span>
            <span>统一建档</span>
          </div>
          <strong>选择剧本文件 <ArrowRight size={16} /></strong>
        </Link>

        <Link className="drama-import-card" to={prototypePaths.dramaParse}>
          <div className="project-type-card__icon"><Layers3 size={22} /></div>
          <h2>上传单集 / 局部剧本</h2>
          <p>适合追加或修订已有项目。可以上传某一集、若干 Scene 或局部模块，并明确新增还是更新。</p>
          <div className="drama-import-card__meta">
            <span>单集 / Scene</span>
            <span>追加内容</span>
            <span>局部 Revision</span>
          </div>
          <strong>选择局部剧本 <ArrowRight size={16} /></strong>
        </Link>
      </div>

      <div className="drama-import__rules">
        <strong>导入规则</strong>
        <span>不会直接覆盖现有生产数据</span>
        <span>先识别范围，再匹配已有 Script</span>
        <span>确认后才创建新 Revision 或新增结构</span>
      </div>
    </div>
  )
}

export function PrototypeDramaParse() {
  const navigate = useNavigate()
  const [shotDraftsAccepted, setShotDraftsAccepted] = useState(false)
  return (
    <div className="prototype__content drama-parse">
      <div className="prototype__page-heading project-create__heading">
        <div>
          <p>漫剧项目 · 第 2 步</p>
          <h1>确认剧本解析结果</h1>
        </div>
        <Link className="prototype__secondary-btn" to={prototypePaths.dramaImport}><ArrowLeft size={15} /> 重新上传</Link>
      </div>

      <div className="drama-parse__summary">
        <div><small>文件</small><strong>午夜便利店_第一季_生产剧本.docx</strong></div>
        <div><small>识别范围</small><strong>完整剧本 · 12 集</strong></div>
        <div><small>处理方式</small><strong>创建 Script Revision 1</strong></div>
        <div><small>解析状态</small><strong className="parse-ok"><Check size={14} /> 已完成</strong></div>
      </div>

      <div className="drama-parse__body">
        <section className="drama-parse__tree">
          <div className="drama-parse__section-head"><strong>结构预览</strong><span>12 Episode · 46 Scene · 182 Shot</span></div>
          <div className="parse-tree-item is-open"><ChevronDown size={14} /><strong>EP01 午夜重逢</strong><span>4 Scene · 15 Shot</span></div>
          <div className="parse-tree-child"><span>S01</span><strong>便利店外 · 雨夜</strong><em>4 Shot · 原剧本已有</em></div>
          <div className="parse-tree-child parse-tree-child--draft"><span>S02</span><strong>便利店内 · 收银区</strong><em>仅 Scene / 对白，缺少 Shot</em></div>
          <div className="parse-tree-child"><span>S03</span><strong>货架通道</strong><em>3 Shot · 原剧本已有</em></div>
          <div className="parse-tree-item"><ChevronRight size={14} /><strong>EP02 未寄出的信</strong><span>5 Scene · 17 Shot</span></div>
          <div className="parse-tree-item"><ChevronRight size={14} /><strong>EP03 深夜电话</strong><span>4 Scene · 14 Shot</span></div>
          <div className="parse-tree-item parse-tree-more"><MoreHorizontal size={14} /><strong>其余 9 集</strong><span>33 Scene</span></div>
        </section>

        <aside className="drama-parse__inspector">
          <div className="drama-parse__section-head"><strong>识别到的核心对象</strong><span>等待确认</span></div>
          <div className="parse-object"><span className="reference-avatar">林</span><div><strong>林夏</strong><small>Character · 出现 11 集</small></div><Check size={15} /></div>
          <div className="parse-object"><span className="reference-avatar">顾</span><div><strong>顾远</strong><small>Character · 出现 10 集</small></div><Check size={15} /></div>
          <div className="parse-object"><span className="reference-avatar reference-avatar--scene">便</span><div><strong>午夜便利店</strong><small>Scene Asset · 出现 7 集</small></div><Check size={15} /></div>
          <div className="parse-object"><span className="reference-avatar reference-avatar--scene">街</span><div><strong>雨夜街口</strong><small>Scene Asset · 出现 3 集</small></div><Check size={15} /></div>
          <div className="drama-parse__notice">
            <strong>不会直接生成角色或场景资产</strong>
            <p>这里只建立身份与引用关系，下一步再绑定已有素材或创建新的 Asset Revision。</p>
          </div>

          <div className="shot-planner-card">
            <div className="shot-planner-card__head">
              <div>
                <small>SHOT PLANNER</small>
                <strong>S02 缺少分镜结构</strong>
              </div>
              <span className="status-pill status-pill--待审核">待确认</span>
            </div>
            <p>检测到该 Scene 只有对白与动作，没有明确 Shot。可以让 AI 生成 Shot 草案，但不会直接写入生产结构。</p>
            <div className="shot-draft-list">
              <div><b>SH01</b><span>中景 · 林夏推门进入便利店</span></div>
              <div><b>SH02</b><span>近景 · 顾远抬头，与林夏对视</span></div>
              <div><b>SH03</b><span>双人中景 · 收银台前短暂停顿</span></div>
              <div><b>SH04</b><span>特写 · 林夏握紧伞柄</span></div>
            </div>
            <div className="shot-planner-card__actions">
              <button className="prototype__secondary-btn">重新生成</button>
              <button
                className={shotDraftsAccepted ? 'prototype__secondary-btn is-confirmed' : 'prototype__primary-btn'}
                onClick={() => setShotDraftsAccepted(true)}
              >
                {shotDraftsAccepted ? <><Check size={15} /> 已确认 Shot 草案</> : '确认 Shot 草案'}
              </button>
            </div>
          </div>
        </aside>
      </div>

      <div className="drama-parse__footer">
        <div>
          <strong>确认后将创建生产结构</strong>
          <span>{shotDraftsAccepted ? 'Script Rev 1 + 已解析 Shot + 已确认 AI Shot 草案。' : '仍有 1 个 AI Shot 草案待确认；已有 Shot 会直接保留。'}</span>
        </div>
        <button className="prototype__primary-btn" disabled={!shotDraftsAccepted} onClick={() => navigate(prototypePaths.characters)}>确认并进入项目 <ArrowRight size={16} /></button>
      </div>
    </div>
  )
}

export function PrototypeCharacters() {
  const [selected, setSelected] = useState<'linxia' | 'store'>('linxia')
  const [boundCharacter, setBoundCharacter] = useState(true)
  const [boundScene, setBoundScene] = useState(false)
  const [showImpact, setShowImpact] = useState(false)

  const selectedIsCharacter = selected === 'linxia'

  return (
    <div className="asset-workspace">
      <div className="storyboard__projectbar">
        <div className="storyboard__project-left">
          <Link className="prototype__icon-btn" to={prototypePaths.projects} aria-label="返回项目"><ArrowLeft size={17} /></Link>
          <div>
            <strong>午夜便利店</strong>
            <span>第 1 集 <ChevronDown size={13} /></span>
          </div>
        </div>
        <div className="storyboard__project-tabs">
          <button>剧本</button>
          <button className="is-active">角色与场景</button>
          <button onClick={() => window.location.assign(prototypePaths.storyboard)}>分镜</button>
          <button>剪辑</button>
          <button>交付</button>
        </div>
        <div className="storyboard__project-actions">
          <span className="save-state"><Check size={13} /> 已保存</span>
          <button className="prototype__secondary-btn">项目概览</button>
        </div>
      </div>

      <div className="asset-workspace__body">
        <aside className="asset-list">
          <div className="asset-list__head">
            <div><strong>角色与场景</strong><span>4 个对象 · 2 个待绑定</span></div>
            <button className="prototype__icon-btn"><Plus size={15} /></button>
          </div>

          <div className="asset-list__group-label">角色</div>
          <button className={selected === 'linxia' ? 'asset-list-item is-active' : 'asset-list-item'} onClick={() => setSelected('linxia')}>
            <span className="reference-avatar">林</span>
            <div><strong>林夏</strong><small>{boundCharacter ? '已绑定 · Rev 3' : '待绑定'}</small></div>
            <span className={boundCharacter ? 'asset-state asset-state--ok' : 'asset-state asset-state--warn'} />
          </button>
          <button className="asset-list-item">
            <span className="reference-avatar">顾</span>
            <div><strong>顾远</strong><small>已绑定 · Rev 2</small></div>
            <span className="asset-state asset-state--ok" />
          </button>

          <div className="asset-list__group-label">场景</div>
          <button className={selected === 'store' ? 'asset-list-item is-active' : 'asset-list-item'} onClick={() => setSelected('store')}>
            <span className="reference-avatar reference-avatar--scene">便</span>
            <div><strong>午夜便利店</strong><small>{boundScene ? '已绑定 · Rev 4' : '待绑定'}</small></div>
            <span className={boundScene ? 'asset-state asset-state--ok' : 'asset-state asset-state--warn'} />
          </button>
          <button className="asset-list-item">
            <span className="reference-avatar reference-avatar--scene">街</span>
            <div><strong>雨夜街口</strong><small>待绑定</small></div>
            <span className="asset-state asset-state--warn" />
          </button>
        </aside>

        <main className="asset-stage">
          <div className="asset-stage__head">
            <div>
              <small>{selectedIsCharacter ? 'CHARACTER' : 'SCENE'}</small>
              <h2>{selectedIsCharacter ? '林夏' : '午夜便利店'}</h2>
            </div>
            <span className={selectedIsCharacter && boundCharacter || !selectedIsCharacter && boundScene ? 'status-pill status-pill--已完成' : 'status-pill status-pill--待审核'}>
              {selectedIsCharacter && boundCharacter || !selectedIsCharacter && boundScene ? '已绑定' : '待绑定'}
            </span>
          </div>

          <div className="asset-stage__preview">
            <div className={selectedIsCharacter ? 'asset-visual asset-visual--character' : 'asset-visual asset-visual--scene'}>
              <span>{selectedIsCharacter ? '角色参考预览' : '场景参考预览'}</span>
            </div>
          </div>

          <div className="asset-stage__actions">
            <button className="prototype__secondary-btn">上传参考</button>
            <button className="prototype__secondary-btn">AI 生成候选</button>
            <button className="prototype__secondary-btn">从全局素材库选择</button>
          </div>

          <div className="asset-consistency">
            <div className="asset-consistency__head">
              <strong>一致性约束</strong>
              <span>这些字段会进入 Shot Reference Binding</span>
            </div>
            {selectedIsCharacter ? (
              <div className="asset-consistency__grid">
                <label><span>发型</span><strong>黑色及肩直发</strong></label>
                <label><span>服装</span><strong>米白风衣 + 深色内搭</strong></label>
                <label><span>主色</span><strong>米白 / 黑 / 灰</strong></label>
                <label><span>禁止变化</span><strong>发色、耳饰、风衣版型</strong></label>
              </div>
            ) : (
              <div className="asset-consistency__grid">
                <label><span>时间</span><strong>深夜 23:00–02:00</strong></label>
                <label><span>光线</span><strong>冷白顶灯 + 暖色招牌</strong></label>
                <label><span>空间布局</span><strong>收银台右侧 / 三列货架</strong></label>
                <label><span>禁止变化</span><strong>收银台位置、招牌色温</strong></label>
              </div>
            )}
          </div>
        </main>

        <aside className="asset-inspector">
          <div className="free-inspector__head"><strong>版本与绑定</strong><MoreHorizontal size={17} /></div>

          <div className="asset-inspector__section">
            <small>业务身份</small>
            <strong>{selectedIsCharacter ? 'Character · chr_linxia' : 'Scene Asset · scn_store'}</strong>
            <span>同一对象保持唯一 ID，修改通过 Revision 表达。</span>
          </div>

          <div className="asset-inspector__section">
            <small>当前项目绑定</small>
            <div className="binding-card">
              <div>
                <strong>{selectedIsCharacter ? 'Global · 林夏角色卡' : 'Global · 午夜便利店场景'}</strong>
                <span>{selectedIsCharacter ? 'Pinned Revision 3' : boundScene ? 'Pinned Revision 4' : '尚未绑定 Revision'}</span>
              </div>
              <button
                className="prototype__secondary-btn"
                onClick={() => selectedIsCharacter ? setBoundCharacter(true) : setBoundScene(true)}
              >
                {selectedIsCharacter && boundCharacter || !selectedIsCharacter && boundScene ? '更换版本' : '绑定素材'}
              </button>
            </div>
          </div>

          <div className="asset-inspector__section">
            <small>Revision 历史</small>
            <div className="revision-row is-current"><b>Rev {selectedIsCharacter ? '3' : boundScene ? '4' : '—'}</b><span>当前项目固定版本</span></div>
            <div className="revision-row"><b>Rev {selectedIsCharacter ? '2' : '3'}</b><span>上一版本</span></div>
            <button className="revision-impact-btn" onClick={() => setShowImpact((v) => !v)}>
              查看升级影响 <ChevronRight size={14} />
            </button>
            {showImpact && (
              <div className="impact-box">
                <strong>升级到新 Revision 将影响</strong>
                <span>EP01 · 6 Shot</span>
                <span>EP02 · 4 Shot</span>
                <span>已采用候选不会自动覆盖，只会标记 stale。</span>
              </div>
            )}
          </div>

          <div className="asset-inspector__section">
            <small>生产门禁</small>
            <p>关键角色与场景未完成绑定前，不允许批量生成相关 Shot。</p>
          </div>
        </aside>
      </div>

      <div className="asset-workspace__footer">
        <div>
          <strong>还有 2 个关键对象待绑定</strong>
          <span>完成后才能进入批量 Shot 生成。</span>
        </div>
        <Link className="prototype__primary-btn" to={prototypePaths.storyboard}>继续到分镜 <ArrowRight size={16} /></Link>
      </div>
    </div>
  )
}

export function PrototypeFreeWorkspace() {
  return (
    <div className="free-workspace">
      <div className="free-workspace__bar">
        <div>
          <Link to={prototypePaths.projects} className="prototype__icon-btn"><ArrowLeft size={17} /></Link>
          <div>
            <strong>秋季新品海报</strong>
            <span>自由创作</span>
          </div>
        </div>
        <div className="free-workspace__tabs">
          <button className="is-active">Canvas</button>
          <button>项目素材</button>
          <button>生成历史</button>
        </div>
        <button className="prototype__secondary-btn">分享项目</button>
      </div>

      <div className="free-workspace__body">
        <aside className="free-toolrail">
          <small>创作工具</small>
          <button><Image size={18} /><span>图片</span></button>
          <button><Film size={18} /><span>视频</span></button>
          <button><Mic2 size={18} /><span>TTS</span></button>
          <button><Shapes size={18} /><span>3D</span></button>
          <button><UserRound size={18} /><span>虚拟人</span></button>
        </aside>

        <main className="free-canvas">
          <div className="free-canvas__hint">Canvas · 可自由组织素材与生成结果</div>
          <div className="canvas-node canvas-node--image">
            <div className="canvas-node__visual"><Sparkles size={22} /></div>
            <div><strong>新品主视觉</strong><span>图片 · 已采用</span></div>
          </div>
          <div className="canvas-node canvas-node--video">
            <div className="canvas-node__visual"><Play size={22} /></div>
            <div><strong>6 秒动态展示</strong><span>视频 · 候选 2</span></div>
          </div>
          <div className="canvas-link-line" />
          <button className="canvas-add"><Plus size={18} /> 添加生成</button>
        </main>

        <aside className="free-inspector">
          <div className="free-inspector__head"><strong>项目上下文</strong><MoreHorizontal size={17} /></div>
          <div className="free-inspector__section">
            <small>当前项目</small>
            <strong>秋季新品海报</strong>
            <span>自由创作项目不要求 Scene / Shot 等生产对象。</span>
          </div>
          <div className="free-inspector__section">
            <small>已绑定素材</small>
            <div className="free-asset-row"><span>产</span><div><strong>产品参考图</strong><em>Project Asset · Rev 2</em></div><Check size={15} /></div>
            <div className="free-asset-row"><span>风</span><div><strong>秋日极简风格</strong><em>Global Asset · pinned Rev 4</em></div><Check size={15} /></div>
          </div>
          <div className="free-inspector__section">
            <small>最近生成</small>
            <p>所有生成仍记录 Attempt、Artifact、模型与成本，可随时回溯。</p>
          </div>
        </aside>
      </div>
    </div>
  )
}

export function PrototypeStoryboard() {
  const [selectedShot, setSelectedShot] = useState('SH02')
  const [selectedCandidate, setSelectedCandidate] = useState<'A' | 'B'>('A')
  const [redoOpen, setRedoOpen] = useState(false)
  const [accepted, setAccepted] = useState<string[]>(['SH01'])
  const navigate = useNavigate()
  const shotIndex = shots.findIndex((shot) => shot.id === selectedShot)
  const current = shots[shotIndex]

  const moveShot = (delta: number) => {
    const next = shots[Math.max(0, Math.min(shots.length - 1, shotIndex + delta))]
    setSelectedShot(next.id)
    setRedoOpen(false)
  }

  return (
    <div className="storyboard">
      <div className="storyboard__projectbar">
        <div className="storyboard__project-left">
          <button className="prototype__icon-btn" onClick={() => navigate(prototypePaths.projects)} aria-label="返回项目"><ArrowLeft size={17} /></button>
          <div>
            <strong>午夜便利店</strong>
            <span>第 1 集 <ChevronDown size={13} /></span>
          </div>
        </div>
        <div className="storyboard__project-tabs">
          <button>故事</button>
          <button>角色与场景</button>
          <button className="is-active">分镜</button>
          <button>剪辑</button>
          <button>交付</button>
        </div>
        <div className="storyboard__project-actions">
          <span className="save-state"><Check size={14} /> 已保存</span>
          <span>预算 ¥120 / ¥300</span>
          <button className="prototype__secondary-btn"><Play size={14} /> 预览</button>
          <button className="prototype__primary-btn">导出</button>
        </div>
      </div>

      <div className="storyboard__workspace">
        <aside className="shot-list">
          <div className="shot-list__head">
            <div><strong>第 1 集</strong><span>8 个镜头</span></div>
            <button><Plus size={15} /></button>
          </div>
          <div className="scene-label"><ChevronDown size={14} /> S01 · 雨夜便利店</div>
          <div className="shot-list__items">
            {shots.map((shot) => (
              <button
                key={shot.id}
                className={`shot-item${selectedShot === shot.id ? ' is-active' : ''}`}
                onClick={() => { setSelectedShot(shot.id); setRedoOpen(false) }}
              >
                <div className="shot-item__thumb"><Film size={16} /></div>
                <div className="shot-item__copy">
                  <strong>{shot.id}</strong>
                  <span>{shot.label}</span>
                </div>
                <i className={`shot-state shot-state--${accepted.includes(shot.id) ? 'approved' : shot.tone}`} title={accepted.includes(shot.id) ? '已采用' : shot.status} />
              </button>
            ))}
          </div>
          <button className="shot-list__batch"><Wand2 size={15} /> 批量报价与生成</button>
        </aside>

        <main className="candidate-stage">
          <div className="candidate-stage__head">
            <div>
              <span className={`status-pill status-pill--待审核`}>{accepted.includes(selectedShot) ? '已采用' : current.status}</span>
              <h2>{current.id} · {current.label}</h2>
            </div>
            <div className="candidate-stage__nav">
              <button disabled={shotIndex === 0} onClick={() => moveShot(-1)}><ArrowLeft size={16} /> 上一镜</button>
              <button disabled={shotIndex === shots.length - 1} onClick={() => moveShot(1)}>下一镜 <ArrowRight size={16} /></button>
            </div>
          </div>

          <div className="candidate-toolbar">
            <div>
              <button className={selectedCandidate === 'A' ? 'is-active' : ''} onClick={() => setSelectedCandidate('A')}>候选 A</button>
              <button className={selectedCandidate === 'B' ? 'is-active' : ''} onClick={() => setSelectedCandidate('B')}>候选 B</button>
            </div>
            <button><Menu size={15} /> 对比模式</button>
          </div>

          <div className={`candidate-preview candidate-preview--${selectedCandidate.toLowerCase()}`}>
            <div className="candidate-preview__scene">
              <span className="candidate-preview__rain rain-one" />
              <span className="candidate-preview__rain rain-two" />
              <div className="candidate-preview__sign">24h</div>
              <div className="candidate-preview__figure" />
              <div className="candidate-preview__caption">
                <small>{selectedCandidate === 'A' ? '候选 A · 6.2s' : '候选 B · 6.0s'}</small>
                <strong>“欢迎光临。”</strong>
              </div>
            </div>
            <button className="candidate-preview__play"><Play size={22} fill="currentColor" /></button>
          </div>

          <div className="review-actions">
            <button
              className="review-actions__accept"
              onClick={() => {
                setAccepted((items) => Array.from(new Set([...items, selectedShot])))
                setRedoOpen(false)
              }}
            >
              <Check size={17} /> 采用这个候选
            </button>
            <button onClick={() => setRedoOpen((open) => !open)}><RefreshCw size={16} /> 局部重做</button>
            <button>标记问题</button>
          </div>

          {redoOpen && (
            <div className="redo-panel">
              <div>
                <strong>局部重做</strong>
                <span>选择要保留的内容，其余部分重新生成。</span>
              </div>
              <label><input type="checkbox" defaultChecked /> 保留角色</label>
              <label><input type="checkbox" defaultChecked /> 保留服装</label>
              <label><input type="checkbox" defaultChecked /> 保留场景</label>
              <label><input type="checkbox" /> 保留运镜</label>
              <button className="prototype__primary-btn">生成新候选</button>
            </div>
          )}
        </main>

        <aside className="shot-inspector">
          <div className="shot-inspector__head"><strong>镜头设置</strong><button><MoreHorizontal size={17} /></button></div>
          <label>
            <span>镜头描述</span>
            <textarea defaultValue="林夏推开便利店玻璃门，镜头从门外缓慢推进。雨水沿玻璃滑落，顾远在收银台后抬头。" />
          </label>
          <div className="inspector-grid">
            <label><span>景别</span><button>中景 <ChevronDown size={13} /></button></label>
            <label><span>机位</span><button>平视 <ChevronDown size={13} /></button></label>
            <label><span>运动</span><button>缓慢推进 <ChevronDown size={13} /></button></label>
            <label><span>时长</span><button>6 秒 <ChevronDown size={13} /></button></label>
          </div>

          <div className="inspector-section">
            <div><strong>角色与场景</strong><button>编辑</button></div>
            <div className="reference-row"><div className="reference-avatar">林</div><div><strong>林夏</strong><span>雨夜外套 · 已批准</span></div><Check size={15} /></div>
            <div className="reference-row"><div className="reference-avatar reference-avatar--scene">店</div><div><strong>便利店</strong><span>雨夜 · 已批准</span></div><Check size={15} /></div>
          </div>

          <div className="inspector-section">
            <div><strong>对白</strong><button>编辑</button></div>
            <blockquote>“欢迎光临。”</blockquote>
          </div>

          <button className="advanced-toggle">高级生成参数 <ChevronRight size={14} /></button>
        </aside>
      </div>

      <div className="storyboard__jobbar">
        <div><span className="job-dot" /><strong>本批生成 8 / 12</strong></div>
        <div className="storyboard__job-progress"><span style={{ width: '66%' }} /></div>
        <span>预计剩余 6 分钟</span>
        <span>已用 ¥12.40</span>
        <button>查看任务</button>
      </div>
    </div>
  )
}

export function PrototypePlaceholder() {
  const location = useLocation()
  const label = location.pathname.endsWith('/assets') ? '素材库' : location.pathname.endsWith('/jobs') ? '生成任务' : '设置'
  return (
    <div className="prototype__content prototype-placeholder">
      <div className="prototype__page-heading"><div><p>原型占位页</p><h1>{label}</h1></div></div>
      <div className="prototype-placeholder__card">
        <Layers3 size={24} />
        <h2>{label}将在下一轮原型中细化</h2>
        <p>本轮先验证首页、项目页、分镜工作区的信息架构与核心交互。</p>
        <Link to={prototypePaths.home}>返回首页</Link>
      </div>
    </div>
  )
}
