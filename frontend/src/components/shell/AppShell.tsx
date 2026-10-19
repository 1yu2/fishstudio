import { useState } from 'react'
import { FolderKanban, Home, LogOut, Menu, Moon, Settings, Sun, X } from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { logout } from '../../api'
import { usePreferences } from '../../utils/preferences'
import { Avatar } from '../ui/Avatar'
import { confirmDialog } from '../ui/ConfirmDialog'
import { paths } from '../../routePaths'
import './AppShell.css'

type ThemeMode = 'dark' | 'light'

interface AppShellProps {
  theme: ThemeMode
  onToggleTheme: () => void
}

const NAV_ITEMS = [
  { to: paths.home, label: 'Home', icon: Home, end: true },
  { to: paths.projects, label: 'Projects', icon: FolderKanban, end: false },
  { to: paths.settings, label: 'Settings', icon: Settings, end: false },
]

function pageTitle(pathname: string): { title: string; subtitle?: string } {
  if (pathname.startsWith('/studio/')) return { title: '节点画布', subtitle: 'Node Canvas' }
  if (pathname.startsWith('/canvas/')) return { title: '自由画布', subtitle: 'Freeform Workspace' }
  if (pathname === paths.dramaHome) return { title: '漫剧项目', subtitle: '创建与管理 Drama 项目' }
  if (pathname.startsWith(`${paths.dramaHome}/`)) return { title: '漫剧工作台', subtitle: 'Drama Workspace' }
  if (pathname === paths.projects) return { title: 'Projects', subtitle: '所有创作项目' }
  if (pathname === paths.settings) return { title: 'Settings', subtitle: '系统与创作环境' }
  return { title: 'Home', subtitle: 'FishStudio' }
}

export default function AppShell({ theme, onToggleTheme }: AppShellProps) {
  const location = useLocation()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [prefs] = usePreferences()
  const heading = pageTitle(location.pathname)
  const workspaceRoute = location.pathname.startsWith('/canvas/') || location.pathname.startsWith('/studio/') || location.pathname.startsWith(`${paths.dramaHome}/`)

  const onLogout = async () => {
    setUserMenuOpen(false)
    if (await confirmDialog({ title: '确定要退出登录吗？', confirmText: '退出登录' })) logout()
  }

  const closeDrawer = () => setDrawerOpen(false)

  return (
    <div className="app-shell">
      {drawerOpen && <button className="app-shell__scrim" aria-label="关闭导航遮罩" onClick={closeDrawer} />}
      <aside className={`app-shell__sidebar${drawerOpen ? ' app-shell__sidebar--open' : ''}`}>
        <div className="app-shell__brand">
          <img src="/logo.png" alt="" />
          <span>FishStudio</span>
          <button className="app-shell__drawer-close" aria-label="关闭导航" onClick={closeDrawer}>
            <X size={18} />
          </button>
        </div>
        <nav className="app-shell__nav" aria-label="全局导航">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={closeDrawer}
              className={({ isActive }) => `app-shell__nav-item${isActive ? ' app-shell__nav-item--active' : ''}`}
            >
              <Icon size={18} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="app-shell__workspace">
        <header className="app-shell__header">
          <div className="app-shell__header-left">
            <button className="app-shell__menu-button" aria-label="打开导航" onClick={() => setDrawerOpen(true)}>
              <Menu size={20} />
            </button>
            <div>
              <div className="app-shell__page-title">{heading.title}</div>
              {heading.subtitle && <div className="app-shell__page-subtitle">{heading.subtitle}</div>}
            </div>
          </div>

          <div className="app-shell__header-actions">
            <button className="app-shell__icon-button" onClick={onToggleTheme} aria-label="切换主题" title="切换主题">
              {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <div className="app-shell__user">
              <button
                className="app-shell__user-button"
                aria-label="用户菜单"
                aria-expanded={userMenuOpen}
                onClick={() => setUserMenuOpen((open) => !open)}
              >
                <Avatar avatarId={prefs.avatarId} nickname={prefs.nickname} size={28} />
                <span>{prefs.nickname}</span>
              </button>
              {userMenuOpen && (
                <>
                  <button className="app-shell__user-mask" aria-label="关闭用户菜单" onClick={() => setUserMenuOpen(false)} />
                  <div className="app-shell__user-menu">
                    <div className="app-shell__user-name">{prefs.nickname}</div>
                    <button onClick={() => void onLogout()}>
                      <LogOut size={15} />
                      退出登录
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </header>

        <main className={`app-shell__content${workspaceRoute ? ' app-shell__content--workspace' : ''}`}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
