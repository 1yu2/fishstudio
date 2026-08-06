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

const objectUrlCache = new Map<string, Promise<string>>()

/** 受保护 URL（/api/...）→ blob object URL；同一 URL 复用进行中的请求。 */
export async function fetchMediaUrl(url: string): Promise<string> {
  let pending = objectUrlCache.get(url)
  if (!pending) {
    pending = (async () => {
      const res = await apiFetch(url)
      if (!res.ok) {
        objectUrlCache.delete(url)
        throw new Error(`媒体加载失败 (${res.status})`)
      }
      const blob = await res.blob()
      return URL.createObjectURL(blob)
    })()
    objectUrlCache.set(url, pending)
  }
  return pending
}

/** 卸载/释放 object URL（组件 unmount 时调用）。 */
export function revokeMediaUrl(url: string, objectUrl: string): void {
  if (objectUrlCache.get(url)) {
    objectUrlCache.delete(url)
    URL.revokeObjectURL(objectUrl)
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
