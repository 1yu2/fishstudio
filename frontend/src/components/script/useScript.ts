import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ConflictError } from '../../api/nodeCanvas'
import { scriptApi, type SaveBase, type ScriptDetail, type ScriptPayload, type ScriptRevisionBrief, type ScriptSource } from '../../api/script'
import { toast } from '../ui/Toast'
import { EMPTY_SCRIPT, ensureBlockIds, wordCount } from './model'

export type SaveState = 'saved' | 'saving' | 'dirty' | 'error'
const SAVE_DELAY = 1000
const newSaveId = () => (typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`)

/**
 * 剧本页的数据：读 GET script，编辑后 1 秒防抖 PUT（带 based_on）；409 时重新拉取并提示。
 * 保存 / beforeunload / pagehide keepalive 的口径与分镜页（useStoryboard）一致。
 */
export function useScript(canvasId: string) {
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [payload, setPayloadState] = useState<ScriptPayload>(EMPTY_SCRIPT)
  const [revision, setRevision] = useState<number | null>(null)
  const [revisions, setRevisions] = useState<ScriptRevisionBrief[]>([])
  const [saveState, setSaveState] = useState<SaveState>('saved')

  const payloadRef = useRef<ScriptPayload>(payload)
  const revisionRef = useRef<number | null>(null)
  const seqRef = useRef<number | null>(null)  // 当前这一版的内容版本（edit_seq）；合并覆盖不改版本号，靠它发现过期写入
  const inflight = useRef<SaveBase | null>(null)  // 在途保存基于的状态，页面关闭的 keepalive 要指向它
  const sourceRef = useRef<ScriptSource>('user')  // 下一次保存的来源：AI 起草 / 导入确认后保存一次，随后回到 user
  const dirty = useRef(false)
  const pending = useRef<Promise<void> | null>(null)
  const saveFailed = useRef(false)

  const apply = useCallback((detail: ScriptDetail) => {
    const next = ensureBlockIds(detail.current?.payload ?? EMPTY_SCRIPT)
    payloadRef.current = next
    revisionRef.current = detail.current?.revision ?? null
    seqRef.current = detail.current?.edit_seq ?? null
    setPayloadState(next)
    setRevision(revisionRef.current)
    setRevisions(detail.revisions)
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoaded(false); setError('')
    scriptApi.get(canvasId).then((detail) => {
      if (cancelled) return
      apply(detail)
      setLoaded(true)
    }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : '剧本加载失败') })
    return () => { cancelled = true }
  }, [canvasId, apply])

  /** 重新拉取（409 后、恢复历史后） */
  const reload = useCallback(async () => {
    apply(await scriptApi.get(canvasId))
    dirty.current = false
    setSaveState('saved')
  }, [canvasId, apply])

  const save = useCallback(async () => {
    if (pending.current) await pending.current
    if (!dirty.current) return
    dirty.current = false
    saveFailed.current = false
    setSaveState('saving')
    const source = sourceRef.current
    sourceRef.current = 'user'
    const request: SaveBase = { revision: revisionRef.current, seq: seqRef.current, saveId: newSaveId() }
    inflight.current = request
    pending.current = scriptApi.save(canvasId, payloadRef.current, request, source)
      .then((saved) => {
        revisionRef.current = saved.revision
        seqRef.current = saved.edit_seq
        setRevision(saved.revision)
        // 同一版被覆盖（合并）时只更新这一条，否则在历史顶部加一条
        const brief: ScriptRevisionBrief = { revision: saved.revision, source: saved.source, word_count: saved.word_count, created_at: saved.updated_at, updated_at: saved.updated_at }
        setRevisions((list) => list.some((item) => item.revision === saved.revision)
          ? list.map((item) => item.revision === saved.revision ? { ...item, ...brief, created_at: item.created_at } : item)
          : [brief, ...list])
        setSaveState(dirty.current ? 'dirty' : 'saved')
      })
      .catch(async (reason) => {
        saveFailed.current = true
        setSaveState('error')
        // 只有保存成功才算清掉未保存状态：任何失败路径都先恢复 dirty，保证离开页面时仍有确认与 keepalive 保护
        dirty.current = true
        if (source !== 'user') sourceRef.current = source
        if (reason instanceof ConflictError) {
          toast('剧本在别处被修改，已刷新', 'error')
          try { await reload() } catch { /* 刷新失败：保持未保存 + 错误状态，下次编辑再试 */ }
        } else {
          toast(reason instanceof Error ? reason.message : '自动保存失败', 'error')
        }
      })
      .finally(() => { pending.current = null; inflight.current = null })
    await pending.current
  }, [canvasId, reload])

  /** 改剧本：fn 拿到当前内容返回新内容，随后防抖保存。 */
  const update = useCallback((fn: (current: ScriptPayload) => ScriptPayload) => {
    const next = fn(payloadRef.current)
    if (next === payloadRef.current) return
    payloadRef.current = next
    setPayloadState(next)
    dirty.current = true
    setSaveState('dirty')
  }, [])

  /** 整份替换（AI 起草 / 粘贴导入确认后）：立即保存，来源记为 ai_draft / import。 */
  const replaceAll = useCallback(async (incoming: ScriptPayload, source: ScriptSource) => {
    const next = ensureBlockIds(incoming)
    payloadRef.current = next
    setPayloadState(next)
    dirty.current = true
    sourceRef.current = source
    setSaveState('dirty')
    await save()
  }, [save])

  useEffect(() => {
    if (saveState !== 'dirty') return
    const timer = window.setTimeout(() => void save(), SAVE_DELAY)
    return () => window.clearTimeout(timer)
  }, [saveState, payload, save])

  useEffect(() => {
    const unsaved = () => dirty.current || !!pending.current
    const confirmLeave = (event: BeforeUnloadEvent) => {
      if (!unsaved()) return
      event.preventDefault()
      event.returnValue = ''
    }
    // 真正离开时用 keepalive 把最新内容发出去。有在途保存时：带上在途保存基于的状态，并用 supersedes 指向它，
    // 服务端在“在途保存已落库”（认 save_id）或“它还没落库、状态仍匹配”两种到达顺序下都会接受
    const sendLatest = () => {
      if (!unsaved()) return
      const flying = inflight.current
      const base: SaveBase = flying
        ? { revision: flying.revision, seq: flying.seq, saveId: newSaveId(), supersedes: flying.saveId }
        : { revision: revisionRef.current, seq: seqRef.current, saveId: newSaveId() }
      scriptApi.saveKeepalive(canvasId, payloadRef.current, base, sourceRef.current)
    }
    window.addEventListener('beforeunload', confirmLeave)
    window.addEventListener('pagehide', sendLatest)
    return () => {
      window.removeEventListener('beforeunload', confirmLeave)
      window.removeEventListener('pagehide', sendLatest)
      if (dirty.current) void save()  // 应用内跳转导致卸载：照常保存
    }
  }, [save, canvasId])

  /** 先把未保存的改动写到服务端；失败就抛错，调用方据此中止（避免按旧内容拆分）。 */
  const flush = useCallback(async () => {
    if (!dirty.current && !pending.current) return
    await save()
    if (saveFailed.current || dirty.current) throw new Error('剧本保存失败，已取消操作，请稍后重试')
  }, [save])

  const restore = useCallback(async (target: number) => {
    await flush()
    await scriptApi.restore(canvasId, target)
    await reload()
  }, [canvasId, flush, apply, reload])

  /** 当前最新内容（含 flush 之后）；调用方不要用渲染时捕获的旧值。 */
  const getPayload = useCallback(() => payloadRef.current, [])

  const words = useMemo(() => wordCount(payload), [payload])
  return { canvasId, loaded, error, payload, revision, revisions, saveState, words, update, replaceAll, flush, restore, reload, getPayload }
}

export type ScriptState = ReturnType<typeof useScript>
