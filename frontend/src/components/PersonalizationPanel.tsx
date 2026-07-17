import type { CSSProperties } from 'react'
import { usePreferences } from '../utils/preferences'
import { Avatar, AVATARS } from './ui/Avatar'
import './PersonalizationPanel.css'

const ACCENTS = [
  { id: 'blue', label: '电光蓝', color: '#2563eb' },
  { id: 'violet', label: '星云紫', color: '#7c3aed' },
  { id: 'green', label: '竹青绿', color: '#059669' },
  { id: 'orange', label: '落日橙', color: '#ea580c' },
  { id: 'pink', label: '品红', color: '#db2777' },
  { id: 'cyan', label: '湖蓝', color: '#0891b2' },
]

const BGS: Array<{ id: string; label: string; style: CSSProperties }> = [
  { id: 'plain', label: '素色', style: { background: 'var(--bg)' } },
  {
    id: 'grid',
    label: '淡网格',
    style: {
      background: 'var(--bg)',
      backgroundImage:
        'linear-gradient(var(--border) 1px, transparent 1px), linear-gradient(90deg, var(--border) 1px, transparent 1px)',
      backgroundSize: '10px 10px',
    },
  },
  {
    id: 'aurora',
    label: '晨雾光斑',
    style: {
      background:
        'radial-gradient(ellipse 70% 60% at 20% 0%, rgba(37,99,235,.15), transparent), radial-gradient(ellipse 60% 50% at 90% 10%, rgba(219,39,119,.12), transparent), var(--bg)',
    },
  },
  {
    id: 'dots',
    label: '细点阵',
    style: {
      background: 'var(--bg)',
      backgroundImage: 'radial-gradient(var(--border) 1.5px, transparent 1.5px)',
      backgroundSize: '10px 10px',
    },
  },
]

export default function PersonalizationPanel() {
  const [prefs, setPrefs] = usePreferences()

  return (
    <div className="personal">
      <section className="personal__section">
        <h3 className="personal__title">个人资料</h3>
        <div className="personal__row">
          <label className="personal__label">昵称</label>
          <input
            className="personal__input"
            value={prefs.nickname}
            maxLength={12}
            placeholder="给自己起个名字"
            onChange={(e) => setPrefs({ nickname: e.target.value })}
            onBlur={(e) => {
              if (!e.target.value.trim()) setPrefs({ nickname: '小鱼' })
            }}
          />
        </div>
        <div className="personal__row">
          <label className="personal__label">头像</label>
          <div className="personal__avatars">
            {AVATARS.map((a) => (
              <button
                key={a.id}
                className={`personal__avatar${prefs.avatarId === a.id ? ' personal__avatar--active' : ''}`}
                onClick={() => setPrefs({ avatarId: a.id })}
                title={a.id}
              >
                {a.emoji}
              </button>
            ))}
            <button
              className={`personal__avatar${prefs.avatarId === 'letter' ? ' personal__avatar--active' : ''}`}
              onClick={() => setPrefs({ avatarId: 'letter' })}
              title="昵称首字"
            >
              <Avatar avatarId="letter" nickname={prefs.nickname} size={30} />
            </button>
          </div>
        </div>
      </section>

      <section className="personal__section">
        <h3 className="personal__title">主题色</h3>
        <p className="personal__desc">全局按钮、链接与高亮颜色</p>
        <div className="personal__accents">
          {ACCENTS.map((a) => (
            <button
              key={a.id}
              className={`personal__accent${prefs.accent === a.id ? ' personal__accent--active' : ''}`}
              onClick={() => setPrefs({ accent: a.id })}
            >
              <span className="personal__accent-dot" style={{ background: a.color }} />
              {a.label}
            </button>
          ))}
        </div>
      </section>

      <section className="personal__section">
        <h3 className="personal__title">背景质感</h3>
        <p className="personal__desc">作用于登录页与首页底层</p>
        <div className="personal__bgs">
          {BGS.map((b) => (
            <button
              key={b.id}
              className={`personal__bg${prefs.bg === b.id ? ' personal__bg--active' : ''}`}
              onClick={() => setPrefs({ bg: b.id })}
            >
              <span className="personal__bg-preview" style={b.style} />
              {b.label}
            </button>
          ))}
        </div>
      </section>

      <section className="personal__section">
        <h3 className="personal__title">首页偏好</h3>
        <div className="personal__row">
          <label className="personal__label">卡片密度</label>
          <select
            className="personal__select"
            value={prefs.homeDensity}
            onChange={(e) => setPrefs({ homeDensity: e.target.value as 'comfortable' | 'compact' })}
          >
            <option value="comfortable">舒适</option>
            <option value="compact">紧凑</option>
          </select>
        </div>
        <div className="personal__row">
          <label className="personal__label">默认排序</label>
          <select
            className="personal__select"
            value={prefs.homeSort}
            onChange={(e) => setPrefs({ homeSort: e.target.value as 'newest' | 'oldest' })}
          >
            <option value="newest">最新优先</option>
            <option value="oldest">最早优先</option>
          </select>
        </div>
        <div className="personal__row">
          <label className="personal__label">默认视图</label>
          <select
            className="personal__select"
            value={prefs.homeView}
            onChange={(e) => setPrefs({ homeView: e.target.value as 'grid' | 'list' })}
          >
            <option value="grid">网格</option>
            <option value="list">列表</option>
          </select>
        </div>
      </section>
    </div>
  )
}
