import { apiFetch } from '../api'
import { ConflictError } from './nodeCanvas'

export interface LibraryLook {
  name: string
  artifact_id: string
  url: string
}

export interface LibraryCharacter {
  id: string
  name: string
  description: string
  looks: LibraryLook[]
  cover_url: string | null
  updated_at: string | null
  /** 给定画布里已有来自这个库角色的节点 */
  in_project: boolean
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const message = body?.error?.message || body?.detail?.message || (typeof body?.detail === 'string' ? body.detail : '') || '请求失败'
    if (response.status === 409) throw new ConflictError(message)
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

const send = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

const project = (canvasId: string) => `/api/workspace/projects/${encodeURIComponent(canvasId)}/characters`

export const libraryApi = {
  /** 我的角色库；给了 canvasId 时带上 in_project */
  list: (canvasId?: string) => json<LibraryCharacter[]>(`/api/library/characters${canvasId ? `?canvas_id=${encodeURIComponent(canvasId)}` : ''}`),
  upload: (body: { name: string; description: string; file: File }) => {
    const form = new FormData()
    form.append('name', body.name)
    form.append('description', body.description)
    form.append('file', body.file)
    return json<LibraryCharacter>('/api/library/characters', { method: 'POST', body: form })
  },
  update: (id: string, body: { name?: string; description?: string }) => json<LibraryCharacter>(`/api/library/characters/${encodeURIComponent(id)}`, send('PATCH', body)),
  remove: (id: string) => json<void>(`/api/library/characters/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  /** 把画布里某个角色（全部已定稿的造型）存进角色库 */
  saveToLibrary: (canvasId: string, character: string) => json<LibraryCharacter>(`${project(canvasId)}/save-to-library`, send('POST', { character })),
  /** 把库角色加入画布；baseRevision 过期时抛 ConflictError */
  addToProject: (canvasId: string, assetId: string, baseRevision: number) =>
    json<{ revision: number; node_ids: string[] }>(`${project(canvasId)}/from-library`, send('POST', { asset_id: assetId, base_revision: baseRevision })),
}
