import { useEffect, useState } from 'react'
import ChatInterface from './components/ChatInterface'
import HomePage from './components/HomePage'
import SettingsPage from './components/SettingsPage'
import './App.css'

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

function readInitialTheme(): ThemeMode {
  try {
    const stored = localStorage.getItem('fishstudio:theme')
    if (stored === 'dark' || stored === 'light') return stored
  } catch {
    // ignore
  }
  return 'dark'
}

function App() {
  const [canvasId, setCanvasId] = useState<string>(() => getCanvasIdFromUrl())
  const [page, setPage] = useState<string>(() => getPageFromUrl())
  const [theme, setTheme] = useState<ThemeMode>(() => readInitialTheme())

  useEffect(() => {
    const onPop = () => {
      setCanvasId(getCanvasIdFromUrl())
      setPage(getPageFromUrl())
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
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

  return (
    <div className="app">
      {page === 'settings' ? (
        <SettingsPage theme={theme} onToggleTheme={toggleTheme} />
      ) : canvasId ? (
        <ChatInterface
          initialCanvasId={canvasId}
          theme={theme}
          onToggleTheme={toggleTheme}
          onSetTheme={setTheme}
        />
      ) : (
        <HomePage theme={theme} onToggleTheme={toggleTheme} />
      )}
    </div>
  )
}

export default App






