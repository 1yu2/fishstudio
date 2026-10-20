/**
 * 受保护媒体加载（review P0-3 前端配合）。
 *
 * 后端 /storage/... 旧路径默认不再公开；新产物走 /api/artifacts/{id}/download
 * （需要 Bearer token）。<video>/<img> 标签无法带 Authorization 头，因此统一
 * 用 apiFetch 风格带 token fetch → blob → URL.createObjectURL。
 *
 * 兼容：老数据只有 /storage/... 路径时，fallback 拼接 ?token=<token> 直接作为
 * src 使用（后端对带 token 的 storage 请求放行）。
 */
import { apiFetch, getToken } from '../api'

interface CacheEntry {
  promise: Promise<string>
  /** 正在使用这个 object URL 的组件数；归零后才释放 */
  users: number
}

const objectUrlCache = new Map<string, CacheEntry>()
/** 最后一个使用者离开后延迟释放：React StrictMode 卸载后会立刻重新挂载，避免重复下载 */
const RELEASE_DELAY_MS = 1000

export interface MediaLease {
  promise: Promise<string>
  /** 不再使用时调用一次；没有其他使用者时释放 object URL */
  release: () => void
}

/**
 * 受保护 URL（/api/...）→ blob object URL。同一 URL 的多个组件共用一份下载；
 * 每个使用者拿到自己的 release，只会归还自己占用的那一份。
 */
export function acquireMediaUrl(url: string): MediaLease {
  let entry = objectUrlCache.get(url)
  if (!entry) {
    const promise = (async () => {
      const res = await apiFetch(url)
      if (!res.ok) throw new Error(`媒体加载失败 (${res.status})`)
      return URL.createObjectURL(await res.blob())
    })()
    const created: CacheEntry = { promise, users: 0 }
    objectUrlCache.set(url, created)
    // 失败的下载不缓存，下次重新请求
    promise.catch(() => { if (objectUrlCache.get(url) === created) objectUrlCache.delete(url) })
    entry = created
  }
  const held = entry
  held.users += 1
  let released = false
  return {
    promise: held.promise,
    release: () => {
      if (released) return
      released = true
      held.users = Math.max(0, held.users - 1)
      if (held.users > 0) return
      window.setTimeout(() => {
        if (held.users > 0) return
        if (objectUrlCache.get(url) === held) objectUrlCache.delete(url)
        held.promise.then((objectUrl) => URL.revokeObjectURL(objectUrl)).catch(() => undefined)
      }, RELEASE_DELAY_MS)
    },
  }
}

/** 是否需要带 token 拉取（相对 /api/ 路径）。 */
export function isProtectedMediaUrl(url: string): boolean {
  return url.startsWith('/api/')
}

/**
 * 老数据 fallback：/storage/... 路径拼 ?token=<token>。
 * 没有 token 时原样返回（依赖 STORAGE_PUBLIC_LEGACY 或 public/ 前缀）。
 */
export function legacyStorageUrl(url: string): string {
  const token = getToken()
  if (!token) return url
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
}
