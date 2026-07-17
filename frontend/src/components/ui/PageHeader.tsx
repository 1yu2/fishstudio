import { useState } from 'react'
import { ArrowLeft, LogOut, Moon, Settings, Sun } from 'lucide-react'
import { logout } from '../../api'
import { usePreferences } from '../../utils/preferences'
import { confirmDialog } from './ConfirmDialog'
import { Avatar } from './Avatar'
import './PageHeader.css'

type ThemeMode = 'dark' | 'light'

interface PageHeaderProps {
  title: string
  subtitle?: string
  theme: ThemeMode
  onToggleTheme: () => void
  /** home：品牌区 + 设置入口；sub：返回按钮 + 标题 */
  variant?: 'home' | 'sub'
}

export function PageHeader({ title, subtitle, theme, onToggleTheme, variant = 'home' }: PageHeaderProps) {
  const [prefs] = usePreferences()
  const [menuOpen, setMenuOpen] = useState(false)

  const goHome = () => {
    const url = new URL(window.location.href)
    url.searchParams.delete('page')
    url.searchParams.delete('canvasId')
    window.history.pushState({}, '', url.toString())
    window.dispatchEvent(new PopStateEvent('popstate'))
  }

  const goToSettings = () => {
    const url = new URL(window.location.href)
    url.searchParams.delete('canvasId')
    url.searchParams.set('page', 'settings')
    window.history.pushState({}, '', url.toString())
    window.dispatchEvent(new PopStateEvent('popstate'))
  }

  const onLogout = async () => {
    setMenuOpen(false)
    if (await confirmDialog({ title: '确定要退出登录吗？', confirmText: '退出登录' })) {
      logout()
    }
  }

  return (
    <header className="page-header">
      <div className="page-header__left">
        {variant === 'sub' ? (
          <>
            <button className="page-header__btn" onClick={goHome}>
              <ArrowLeft size={16} />
              返回
            </button>
            <span className="page-header__title">{title}</span>
          </>
        ) : (
          <div className="page-header__brand">
            <img src="/logo.png" alt="FishStudio" className="page-header__logo" />
            <div>
              <div className="page-header__title">{title}</div>
              {subtitle && <div className="page-header__subtitle">{subtitle}</div>}
            </div>
          </div>
        )}
      </div>

      <div className="page-header__right">
        {variant === 'home' && (
          <button className="page-header__btn" onClick={goToSettings}>
            <Settings size={16} />
            设置
          </button>
        )}
        <button className="page-header__btn" onClick={onToggleTheme} title="切换主题">
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          {theme === 'dark' ? '亮色' : '暗色'}
        </button>
        <div className="page-header__user">
          <button className="page-header__avatar-btn" onClick={() => setMenuOpen((v) => !v)} title={prefs.nickname}>
            <Avatar avatarId={prefs.avatarId} nickname={prefs.nickname} />
          </button>
          {menuOpen && (
            <>
              <div className="page-header__menu-mask" onClick={() => setMenuOpen(false)} />
              <div className="page-header__menu">
                <div className="page-header__menu-name">{prefs.nickname}</div>
                <button className="page-header__menu-item" onClick={onLogout}>
                  <LogOut size={14} />
                  退出登录
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  )
}
