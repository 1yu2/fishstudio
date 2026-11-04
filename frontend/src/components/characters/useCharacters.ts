import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ConflictError, nodeCanvasApi,
  type CanvasModels, type CanvasRuns, type RunRequest, type StoredEdge, type StoredGraph, type StoredNode,
} from '../../api/nodeCanvas'
import { toast } from '../ui/Toast'
import { newRequestId } from '../nodeCanvas/graph'

export type SaveState = 'saved' | 'saving' | 'dirty' | 'error'
const SAVE_DELAY = 800
const POLL_INTERVAL = 3000

interface Graph { nodes: StoredNode[]; edges: StoredEdge[] }

/** 结果和任务状态的指纹：变化时通知项目页刷新步骤条。 */
function runsSignature(runs: CanvasRuns | null): string {
  if (!runs) return ''
  return Object.entries(runs.nodes).map(([id, entry]) => `${id}:${entry.results.length}:${entry.jobs[0]?.status ?? ''}`).sort().join('|')
}

/**
 * 角色页的数据：读写画布 graph（角色节点 data）和画布的 runs，不新建任何表。
 * 保存 / 409 / 轮询 / keepalive 的口径与分镜页 useStoryboard 一致（这里没有批量与预估）。
 */
export function useCharacters(canvasId: string) {
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [aspectRatio, setAspectRatio] = useState('9:16')
  const [graph, setGraphState] = useState<Graph>({ nodes: [], edges: [] })
  const [models, setModels] = useState<CanvasModels | null>(null)
  const [runs, setRuns] = useState<CanvasRuns | null>(null)
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [revision, setRevision] = useState(0)
  const [polling, setPolling] = useState(true)
  const [locked, setLocked] = useState(false)
  const lockedRef = useRef(false)

  const graphRef = useRef<Graph>(graph)
  const viewport = useRef<StoredGraph['viewport']>(undefined)
  const revisionRef = useRef(0)
  const dirty = useRef(false)
  const pending = useRef<Promise<void> | null>(null)
  const saveFailed = useRef(false)

  const applyDetail = useCallback((detail: { graph: StoredGraph; revision: number }) => {
    const next = { nodes: detail.graph.nodes || [], edges: detail.graph.edges || [] }
    graphRef.current = next
    viewport.current = detail.graph.viewport
    revisionRef.current = detail.revision
    setGraphState(next)
    setRevision(detail.revision)
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoaded(false); setError('')
    nodeCanvasApi.get(canvasId).then((value) => {
      if (cancelled) return
      setAspectRatio(value.aspect_ratio)
      applyDetail(value)
      setLoaded(true)
    }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : '角色加载失败') })
    nodeCanvasApi.models().then((value) => { if (!cancelled) setModels(value) }).catch(() => undefined)
    return () => { cancelled = true }
  }, [canvasId, applyDetail])

  // ── 保存（防抖 + 带 revision；409 时重新加载）──
  const save = useCallback(async () => {
    if (pending.current) await pending.current
    if (!dirty.current) return
    dirty.current = false
    saveFailed.current = false
    setSaveState('saving')
    const body: StoredGraph = { nodes: graphRef.current.nodes, edges: graphRef.current.edges, ...(viewport.current ? { viewport: viewport.current } : {}) }
    pending.current = nodeCanvasApi.save(canvasId, body, revisionRef.current)
      .then((result) => { revisionRef.current = result.revision; setRevision(result.revision); setSaveState(dirty.current ? 'dirty' : 'saved') })
      .catch(async (reason) => {
        saveFailed.current = true
        setSaveState('error')
        if (reason instanceof ConflictError) {
          toast('画布在别处被修改，已刷新', 'error')
          try {
            applyDetail(await nodeCanvasApi.get(canvasId))
            dirty.current = false
            setSaveState('saved')
          } catch { /* 刷新失败：保持错误状态，下次编辑再试 */ }
        } else {
          dirty.current = true
          toast(reason instanceof Error ? reason.message : '自动保存失败', 'error')
        }
      })
      .finally(() => { pending.current = null })
    await pending.current
  }, [canvasId, applyDetail])

  /** 改图：fn 拿到当前 graph 返回新 graph，随后防抖保存。 */
  const update = useCallback((fn: (current: Graph) => Graph) => {
    if (lockedRef.current) return  // 加入角色期间画布会被整张重新读取，此时不接受编辑，避免改动被悄悄丢掉
    const next = fn(graphRef.current)
    graphRef.current = next
    setGraphState(next)
    dirty.current = true
    setSaveState('dirty')
  }, [])

  const updateData = useCallback((nodeId: string, patch: Record<string, unknown>) => {
    update((current) => ({ ...current, nodes: current.nodes.map((node) => node.id === nodeId ? { ...node, data: { ...node.data, ...patch } } : node) }))
  }, [update])

  useEffect(() => {
    if (saveState !== 'dirty') return
    const timer = window.setTimeout(() => void save(), SAVE_DELAY)
    return () => window.clearTimeout(timer)
  }, [saveState, graph, save])

  useEffect(() => {
    const unsaved = () => dirty.current || !!pending.current
    // 关闭 / 刷新：有未保存或保存中的改动时让浏览器确认；真正离开（pagehide）时用 keepalive 把最新 graph 发出去。
    // 在途保存 A 之后排队的改动 B 也包含在内；A 成功会让 revision +1，所以在途时带 revision + 1
    const confirmLeave = (event: BeforeUnloadEvent) => {
      if (!unsaved()) return
      event.preventDefault()
      event.returnValue = ''
    }
    const sendLatest = () => {
      if (!unsaved()) return
      const body: StoredGraph = { nodes: graphRef.current.nodes, edges: graphRef.current.edges, ...(viewport.current ? { viewport: viewport.current } : {}) }
      nodeCanvasApi.saveKeepalive(canvasId, body, revisionRef.current + (pending.current ? 1 : 0))
    }
    window.addEventListener('beforeunload', confirmLeave)
    window.addEventListener('pagehide', sendLatest)
    return () => {
      window.removeEventListener('beforeunload', confirmLeave)
      window.removeEventListener('pagehide', sendLatest)
      if (dirty.current) void save()  // 应用内跳转导致卸载：照常保存
    }
  }, [save, canvasId])

  // 先把未保存的改动写到服务端；保存失败就抛错，调用方据此中止，避免按旧图运行、报价或存库
  const flushThen = useCallback(async () => {
    if (!dirty.current && !pending.current) return
    await save()
    if (saveFailed.current || dirty.current) throw new Error('画布保存失败，已取消操作，请稍后重试')
  }, [save])

  // ── 任务进度：有进行中任务时每 3 秒轮询，页面隐藏时暂停 ──
  const runsSeq = useRef(0)
  const refreshRuns = useCallback(async () => {
    const id = ++runsSeq.current
    try {
      const value = await nodeCanvasApi.runs(canvasId)
      if (id !== runsSeq.current) return
      setRuns(value)
      setPolling(value.active_jobs > 0)
    } catch { /* 下次轮询再试 */ }
  }, [canvasId])

  useEffect(() => { void refreshRuns() }, [refreshRuns])
  useEffect(() => {
    if (!polling) return
    const timer = window.setInterval(() => { if (!document.hidden) void refreshRuns() }, POLL_INTERVAL)
    const onVisible = () => { if (!document.hidden) void refreshRuns() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [polling, refreshRuns])

  // ── 单节点：报价与提交 ──
  const quote = useCallback((request: RunRequest) => nodeCanvasApi.quote(canvasId, request), [canvasId])

  const run = useCallback(async (nodeId: string, request: RunRequest): Promise<boolean> => {
    try {
      await flushThen()
      // 每次点击一个新的 client_request_id；网络层失败的自动重试复用它，后端不会重复建任务
      const body = { ...request, client_request_id: request.client_request_id || newRequestId() }
      try { await nodeCanvasApi.run(canvasId, nodeId, body) }
      catch (reason) {
        if (!(reason instanceof TypeError)) throw reason
        await nodeCanvasApi.run(canvasId, nodeId, body)
      }
      await refreshRuns()
      setPolling(true)
      return true
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : '提交失败', 'error')
      return false
    }
  }, [canvasId, flushThen, refreshRuns])

  /** 角色库“加入”需要：先保存，再取当前 revision。 */
  const savedRevision = useCallback(async () => { await flushThen(); return revisionRef.current }, [flushThen])

  /**
   * 服务端要整张改写画布的操作（从角色库加入）：期间页面锁定编辑（locked），先保存已有改动，
   * 结束后重新读取；这样不会有“请求期间改的内容被 reload 覆盖”的情况。
   */
  const withLock = useCallback(async <T,>(fn: () => Promise<T>): Promise<T> => {
    lockedRef.current = true
    setLocked(true)
    try { return await fn() }
    finally { lockedRef.current = false; setLocked(false) }
  }, [])

  /** 服务端改了画布（从角色库加入 / 别处修改）后，重新读取 graph 与结果。 */
  const reload = useCallback(async () => {
    applyDetail(await nodeCanvasApi.get(canvasId))
    dirty.current = false
    setSaveState('saved')
    await refreshRuns()
  }, [canvasId, applyDetail, refreshRuns])

  const signature = runsSignature(runs)
  return useMemo(() => ({
    canvasId, loaded, error, aspectRatio, nodes: graph.nodes, edges: graph.edges, models, runs, saveState, revision, signature, locked,
    withLock, update, updateData, flushThen, quote, run, savedRevision, reload, refreshRuns,
  }), [
    canvasId, loaded, error, aspectRatio, graph, models, runs, saveState, revision, signature, locked,
    withLock, update, updateData, flushThen, quote, run, savedRevision, reload, refreshRuns,
  ])
}

export type CharactersData = ReturnType<typeof useCharacters>
