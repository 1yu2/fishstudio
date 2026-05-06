/**
 * 统一的 API fetch 封装：自动注入 Authorization 头；401 时清 token 并跳登录。
 *
 * 用法：把项目里 `fetch('/api/...', ...)` 替换成 `apiFetch('/api/...', ...)`。
 * 静态资源（/storage/...）保持原生 fetch 即可。
 */
const TOKEN_KEY = 'fishstudio:auth_token'
const USER_KEY = 'fishstudio:auth_user'

export type AuthUser = {
  id: number
  username: string
  is_admin: boolean
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token)
  } catch {
    // ignore
  }
}

export function clearAuth(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  } catch {
    // ignore
  }
}

export function getStoredUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as AuthUser) : null
  } catch {
    return null
  }
}

export function setStoredUser(user: AuthUser): void {
  try {
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  } catch {
    // ignore
  }
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const token = getToken()
  const headers = new Headers(init.headers || {})
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  const res = await fetch(input, { ...init, headers })
  if (res.status === 401) {
    clearAuth()
    // 通知 App 重新渲染到登录页
    window.dispatchEvent(new CustomEvent('fishstudio:unauthorized'))
  }
  return res
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(detail || `登录失败 (${res.status})`)
  }
  const data = await res.json()
  setToken(data.access_token)
  setStoredUser(data.user)
  return data.user
}

export function logout(): void {
  clearAuth()
  window.dispatchEvent(new CustomEvent('fishstudio:unauthorized'))
}
