import { useEffect, useState } from 'react'
import { BrowserRouter } from 'react-router-dom'
import LoginPage from './components/LoginPage'
import { getToken } from './api'
import { applyPrefs } from './utils/preferences'
import { Toaster } from './components/ui/Toast'
import { ConfirmHost } from './components/ui/ConfirmDialog'
import { AppRoutes } from './routing'
import './App.css'

type ThemeMode = 'dark' | 'light'

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
  const [theme, setTheme] = useState<ThemeMode>(() => readInitialTheme())
  const [authed, setAuthed] = useState<boolean>(() => Boolean(getToken()))

  useEffect(() => {
    applyPrefs()
  }, [])

  useEffect(() => {
    const onUnauthorized = () => setAuthed(false)
    window.addEventListener('fishstudio:unauthorized', onUnauthorized)
    return () => {
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
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AppRoutes theme={theme} onToggleTheme={toggleTheme} onSetTheme={setTheme} />
      </BrowserRouter>
      <Toaster />
      <ConfirmHost />
    </div>
  )
}

export default App
