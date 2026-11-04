import { useState } from 'react'
import { FolderKanban, LayoutGrid, LogOut, Menu, Moon, Settings, Sun, Users, X } from 'lucide-react'
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
  { to: paths.home, label: '工作台', icon: LayoutGrid, end: true },
  { to: paths.projects, label: '我的项目', icon: FolderKanban, end: false },
  { to: paths.library, label: '角色库', icon: Users, end: false },
  { to: paths.settings, label: '设置', icon: Settings, end: false },
]

export default function AppShell({ theme, onToggleTheme }: AppShellProps) {
  const location = useLocation()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [prefs] = usePreferences()
  // 项目工作区全宽呈现，不套页面内边距
  const workspaceRoute = location.pathname.startsWith('/studio/')

  const onLogout = async () => {
    if (await confirmDialog({ title: '确定要退出登录吗？', confirmText: '退出登录' })) logout()
  }

  const closeDrawer = () => setDrawerOpen(false)

  return (
    <div className="app-shell">
      {drawerOpen && <button className="app-shell__scrim" aria-label="关闭导航遮罩" onClick={closeDrawer} />}
      <aside className={`app-shell__sidebar${drawerOpen ? ' app-shell__sidebar--open' : ''}`}>
        <div className="app-shell__brand">
          <span className="app-shell__logo" aria-hidden="true">F</span>
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
              <Icon size={16} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="app-shell__account">
          <Avatar avatarId={prefs.avatarId} nickname={prefs.nickname} size={28} />
          <span className="app-shell__account-name">{prefs.nickname}</span>
          <button className="app-shell__icon-button" onClick={onToggleTheme} aria-label="切换主题" title="切换主题">
            {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          </button>
          <button className="app-shell__icon-button" onClick={() => void onLogout()} aria-label="退出登录" title="退出登录">
            <LogOut size={15} />
          </button>
        </div>
      </aside>

      <div className="app-shell__workspace">
        {/* 仅手机宽度显示：唯一用途是打开导航抽屉 */}
        <div className="app-shell__mobilebar">
          <button className="app-shell__menu-button" aria-label="打开导航" onClick={() => setDrawerOpen(true)}>
            <Menu size={20} />
          </button>
          <span>FishStudio</span>
        </div>
        <main className={`app-shell__content${workspaceRoute ? ' app-shell__content--workspace' : ''}`}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
