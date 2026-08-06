import { Suspense, lazy, useEffect, useState } from 'react'
import HomePage from './components/HomePage'
import SettingsPage from './components/SettingsPage'
import LoginPage from './components/LoginPage'
import { getToken } from './api'
import { applyPrefs } from './utils/preferences'
import { Toaster } from './components/ui/Toast'
import { ConfirmHost } from './components/ui/ConfirmDialog'
import './App.css'

// 路由级懒加载（P2-8）：重组件按 ?page= 切换时按需拆分 chunk。
// ChatInterface 内部引入 Excalidraw 画布 / 3D 预览 / Mermaid，随之一并拆出。
const ChatInterface = lazy(() => import('./components/ChatInterface'))
const StudioHome = lazy(() => import('./components/studio/StudioHome'))
const StudioPage = lazy(() => import('./components/studio/StudioPage'))

const pageFallback = <div style={{ padding: 24, color: 'var(--text-secondary, #6b7280)' }}>加载中…</div>

type ThemeMode = 'dark' | 'light'

function getCanvasIdFromUrl() {
  try {
    const url = new URL(window.location.href)
    return url.searchParams.get('canvasId') || ''
  } catch {
    return ''
  }
}

function getPageFromUrl() {
  try {
    const url = new URL(window.location.href)
    return url.searchParams.get('page') || ''
  } catch {
    return ''
  }
}

function getStudioProjectIdFromUrl() {
  try {
    const url = new URL(window.location.href)
    return url.searchParams.get('projectId') || ''
  } catch {
    return ''
  }
}

function readInitialTheme(): ThemeMode {
  try {
    const stored = localStorage.getItem('fishstudio:theme')
    if (stored === 'dark' || stored === 'light') return stored
  } catch {
    // ignore
  }
  return 'light' // 默认浅色（B · 白昼工作室），已存偏好不受影响
}

function App() {
  const [canvasId, setCanvasId] = useState<string>(() => getCanvasIdFromUrl())
  const [page, setPage] = useState<string>(() => getPageFromUrl())
  const [studioProjectId, setStudioProjectId] = useState<string>(() => getStudioProjectIdFromUrl())
  const [theme, setTheme] = useState<ThemeMode>(() => readInitialTheme())
  const [authed, setAuthed] = useState<boolean>(() => Boolean(getToken()))

  useEffect(() => {
    applyPrefs()
  }, [])

  useEffect(() => {
    const onPop = () => {
      setCanvasId(getCanvasIdFromUrl())
      setPage(getPageFromUrl())
      setStudioProjectId(getStudioProjectIdFromUrl())
    }
    const onUnauthorized = () => setAuthed(false)
    window.addEventListener('popstate', onPop)
    window.addEventListener('fishstudio:unauthorized', onUnauthorized)
    return () => {
      window.removeEventListener('popstate', onPop)
      window.removeEventListener('fishstudio:unauthorized', onUnauthorized)
    }
  }, [])

  useEffect(() => {
    try {
      document.documentElement.dataset.theme = theme
      localStorage.setItem('fishstudio:theme', theme)
    } catch {
      // ignore
    }
  }, [theme])

  const toggleTheme = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))

  if (!authed) {
    return (
      <div className="app">
        <LoginPage onLoggedIn={() => setAuthed(true)} />
        <Toaster />
        <ConfirmHost />
      </div>
    )
  }

  return (
    <div className="app">
      {page === 'settings' ? (
        <SettingsPage theme={theme} onToggleTheme={toggleTheme} />
      ) : page === 'studio' ? (
        <Suspense fallback={pageFallback}>
          {studioProjectId ? (
            <StudioPage projectId={studioProjectId} theme={theme} onToggleTheme={toggleTheme} />
          ) : (
            <StudioHome theme={theme} onToggleTheme={toggleTheme} />
          )}
        </Suspense>
      ) : canvasId ? (
        <Suspense fallback={pageFallback}>
          <ChatInterface
            initialCanvasId={canvasId}
            theme={theme}
            onToggleTheme={toggleTheme}
            onSetTheme={setTheme}
          />
        </Suspense>
      ) : (
        <HomePage theme={theme} onToggleTheme={toggleTheme} />
      )}
      <Toaster />
      <ConfirmHost />
    </div>
  )
}

export default App
