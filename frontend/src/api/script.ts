import { apiFetch, getToken } from '../api'
import { ConflictError } from './nodeCanvas'

export type ScriptSource = 'user' | 'ai_draft' | 'import' | 'restore'

export interface ScriptBlock {
  /** 客户端生成的块 id，稳定地标识一个块（key、改写建议定位）；服务端只校验唯一 */
  id: string
  type: 'action' | 'dialogue'
  /** 仅对白块使用 */
  character: string
  text: string
}

export interface ScriptScene {
  id: string
  title: string
  heading: string
  blocks: ScriptBlock[]
}

export interface ScriptAct {
  title: string
  scenes: ScriptScene[]
}

export interface ScriptPayload {
  acts: ScriptAct[]
}

export interface ScriptRevision {
  revision: number
  payload: ScriptPayload
  source: ScriptSource
  word_count: number
  /** 这一版的内容版本，每次保存（含合并覆盖）+1；保存时作为 based_on_seq 回传 */
  edit_seq: number
  updated_at: string | null
}

export interface ScriptRevisionBrief {
  revision: number
  source: ScriptSource
  word_count: number
  created_at: string | null
  updated_at: string | null
}

export interface ScriptDetail {
  current: ScriptRevision | null
  revisions: ScriptRevisionBrief[]
}

export type RewriteMode = 'rewrite' | 'shorten' | 'tone'

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const message = body?.error?.message || body?.detail?.message || (typeof body?.detail === 'string' ? body.detail : '') || '请求失败'
    if (response.status === 409) throw new ConflictError(message)
    throw new Error(message)
  }
  return response.json()
}

const send = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
})

/** 保存基于的状态：版本号 + 内容版本；saveId 标识本次请求，supersedes 指向在途保存（keepalive 用）。 */
export interface SaveBase {
  revision: number | null
  seq: number | null
  saveId: string
  supersedes?: string
}

const saveBody = (payload: ScriptPayload, base_: SaveBase, source: ScriptSource) => ({
  payload, based_on: base_.revision, based_on_seq: base_.seq, source, save_id: base_.saveId,
  ...(base_.supersedes ? { supersedes: base_.supersedes } : {}),
})

const base = (id: string) => `/api/workspace/projects/${encodeURIComponent(id)}/script`

export const scriptApi = {
  get: (id: string) => json<ScriptDetail>(base(id)),
  revision: (id: string, revision: number) => json<ScriptRevision>(`${base(id)}/revisions/${revision}`),
  save: (id: string, payload: ScriptPayload, base_: SaveBase, source: ScriptSource = 'user') =>
    json<ScriptRevision>(base(id), send('PUT', saveBody(payload, base_, source))),
  /** 页面关闭时的尽力保存：keepalive 让请求在页面卸载后继续发送（不等待、不处理结果）。 */
  saveKeepalive: (id: string, payload: ScriptPayload, base_: SaveBase, source: ScriptSource = 'user') => {
    const token = getToken()
    void fetch(base(id), {
      method: 'PUT', keepalive: true,
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(saveBody(payload, base_, source)),
    }).catch(() => undefined)
  },
  restore: (id: string, revision: number) => json<ScriptRevision>(`${base(id)}/restore`, send('POST', { revision })),
  /** 规则解析纯文本（不调用 AI，不保存） */
  importText: (id: string, text: string) => json<{ payload: ScriptPayload }>(`${base(id)}/import`, send('POST', { text })),
  /** AI 按想法起草剧本（会调用 AI，不保存） */
  draft: (id: string, idea: string, scenes: number) => json<{ payload: ScriptPayload }>(`${base(id)}/draft`, send('POST', { idea, scenes })),
  /** AI 改写选中文字（会调用 AI，不保存） */
  rewrite: (id: string, body: { text: string; mode: RewriteMode; tone?: string; context?: string }) =>
    json<{ text: string }>(`${base(id)}/rewrite`, send('POST', body)),
}
