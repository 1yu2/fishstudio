import { useSyncExternalStore } from 'react'

export interface Prefs {
  nickname: string                        // 默认 "小鱼"
  avatarId: string                        // fish|fox|panda|lion|rabbit|whale|chick|letter
  accent: string                          // blue|violet|green|orange|pink|cyan
  bg: string                              // plain|grid|aurora|dots
  homeDensity: 'comfortable' | 'compact'
  homeSort: 'newest' | 'oldest'
  homeView: 'grid' | 'list'
  favorites: string[]                     // 收藏的项目 canvasId
}

const KEY = 'fishstudio:prefs'

export const DEFAULT_PREFS: Prefs = {
  nickname: '小鱼',
  avatarId: 'fish',
  accent: 'blue',
  bg: 'plain',
  homeDensity: 'comfortable',
  homeSort: 'newest',
  homeView: 'grid',
  favorites: [],
}

let current: Prefs = load()
const listeners = new Set<() => void>()

function load(): Prefs {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return { ...DEFAULT_PREFS, ...(JSON.parse(raw) as Partial<Prefs>) }
  } catch {
    // 解析失败则用默认值
  }
  return { ...DEFAULT_PREFS }
}

function emit() {
  listeners.forEach((l) => l())
}

export function getPrefs(): Prefs {
  return current
}

export function setPrefs(patch: Partial<Prefs>): void {
  current = { ...current, ...patch }
  localStorage.setItem(KEY, JSON.stringify(current))
  applyPrefs()
  emit()
}

export function toggleFavorite(canvasId: string): void {
  const favorites = current.favorites.includes(canvasId)
    ? current.favorites.filter((id) => id !== canvasId)
    : [...current.favorites, canvasId]
  setPrefs({ favorites })
}

/** 把 accent / bg 偏好写到 <html> 的 data 属性上，供 tokens.css 的选择器命中 */
export function applyPrefs(): void {
  const root = document.documentElement
  root.dataset.accent = current.accent
  root.dataset.bg = current.bg
}

/** 一次性迁移旧 localStorage key（不删旧 key，旧代码读旧 key 不受影响） */
export function migrateLegacyKeys(): void {
  const moves: Array<[string, string]> = [
    ['ai_agent_current_canvas_id', 'fishstudio:current_canvas_id'],
    ['fishstudio_workspace_seen', 'fishstudio:workspace_seen'],
  ]
  for (const [oldKey, newKey] of moves) {
    const v = localStorage.getItem(oldKey)
    if (v !== null && localStorage.getItem(newKey) === null) {
      localStorage.setItem(newKey, v)
    }
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function usePreferences(): [Prefs, (patch: Partial<Prefs>) => void] {
  const prefs = useSyncExternalStore(subscribe, getPrefs)
  return [prefs, setPrefs]
}
