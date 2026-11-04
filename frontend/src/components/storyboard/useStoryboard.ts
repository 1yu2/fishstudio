import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ConflictError, nodeCanvasApi,
  type BatchKind, type BatchQuote, type BatchRun, type BatchScope, type CanvasModels, type CanvasRuns, type RunRequest, type StoredEdge, type StoredGraph, type StoredNode,
} from '../../api/nodeCanvas'
import { toast } from '../ui/Toast'
import { newRequestId } from '../nodeCanvas/graph'
import type { BatchPlan } from '../nodeCanvas/BatchDialog'

export type SaveState = 'saved' | 'saving' | 'dirty' | 'error'
const SAVE_DELAY = 800
const POLL_INTERVAL = 3000
const ESTIMATE_DELAY = 500

interface Graph { nodes: StoredNode[]; edges: StoredEdge[] }

/** 分段批量的预估：进入页面和镜头变化后预取，按钮上直接显示价格。 */
export interface Estimate { state: 'loading' | 'ready' | 'error'; quote: BatchQuote | null }
const LOADING: Estimate = { state: 'loading', quote: null }

/** 结果和任务状态的指纹：变化时重新预估（已有结果的节点会被批量跳过）。 */
function runsSignature(runs: CanvasRuns | null): string {
  if (!runs) return ''
  return Object.entries(runs.nodes).map(([id, entry]) => `${id}:${entry.results.length}:${entry.jobs[0]?.status ?? ''}`).sort().join('|')
}

/**
 * 分镜页的数据：读写画布 graph（节点 data）和画布的 runs，不新建任何表。
 * 与 NodeCanvasPage 各自独立（不共享 react-flow 状态），保存 / 轮询 / 批量的口径保持一致。
 */
export function useStoryboard(canvasId: string) {
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [title, setTitle] = useState('')
  const [aspectRatio, setAspectRatio] = useState('9:16')
  const [graph, setGraphState] = useState<Graph>({ nodes: [], edges: [] })
  const [models, setModels] = useState<CanvasModels | null>(null)
  const [runs, setRuns] = useState<CanvasRuns | null>(null)
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [revision, setRevision] = useState(0)
  const [polling, setPolling] = useState(true)
  const [batch, setBatch] = useState<BatchRun | null>(null)
  const [plan, setPlan] = useState<BatchPlan | null>(null)
  const [hintDismissed, setHintDismissed] = useState<string | null>(null)
  const [estimates, setEstimates] = useState<Record<BatchKind, Estimate>>({ image: LOADING, video: LOADING })

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
      setTitle(value.title); setAspectRatio(value.aspect_ratio)
      applyDetail(value)
      setLoaded(true)
    }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : '分镜加载失败') })
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
    // 在途保存 A 之后排队的改动 B 也包含在内（发的是最新 graph，不等 A）；A 成功会让 revision +1，所以在途时带 revision + 1
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

  // 先把未保存的改动写到服务端；保存失败就抛错，调用方据此中止，避免按旧图运行或报价
  const flushThen = useCallback(async () => {
    if (!dirty.current && !pending.current) return
    await save()
    if (saveFailed.current || dirty.current) throw new Error('画布保存失败，已取消操作，请稍后重试')
  }, [save])

  // ── 任务进度：有进行中任务时每 3 秒轮询，页面隐藏时暂停 ──
  // 只采用最新发出的请求的响应：旧响应晚到时既不覆盖 runs，也不能据此停掉轮询
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

  // ── 批量运行 ──
  useEffect(() => {
    void (async () => {
      try {
        const [latest] = await nodeCanvasApi.batchList(canvasId)
        if (latest) setBatch(latest)
      } catch { /* 没有也不影响使用 */ }
    })()
  }, [canvasId])

  const batchId = batch?.status === 'running' ? batch.id : null
  useEffect(() => {
    if (!batchId) return
    const controller = new AbortController()
    const timer = window.setInterval(() => {
      if (document.hidden) return
      void nodeCanvasApi.batchGet(canvasId, batchId, controller.signal).then((value) => {
        if (controller.signal.aborted) return
        setBatch((current) => current && current.id === value.id ? value : current)
        void refreshRuns()
      }).catch(() => undefined)
    }, POLL_INTERVAL)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [batchId, canvasId, refreshRuns])

  const loadQuote = useCallback(async (scope: BatchScope, kinds?: BatchKind[], notice = '') => {
    const next: BatchPlan = { scope, kinds, requestId: newRequestId(), quote: null, error: '', notice, busy: false }
    setPlan(next)
    const apply = (patch: Partial<BatchPlan>) => setPlan((current) => current?.requestId === next.requestId ? { ...current, ...patch } : current)
    try {
      await flushThen()
      apply({ quote: await nodeCanvasApi.batchQuote(canvasId, { scope, ...(kinds ? { kinds } : {}) }) })
    } catch (reason) {
      apply({ error: reason instanceof Error ? reason.message : '报价失败' })
    }
  }, [canvasId, flushThen])

  const openBatch = useCallback((kinds: BatchKind[]) => loadQuote('all', kinds), [loadQuote])

  const confirmBatch = useCallback(async () => {
    if (!plan || plan.busy || !plan.quote) return
    const current = plan
    setPlan({ ...current, busy: true, error: '' })
    try {
      await flushThen()
      const started = await nodeCanvasApi.batchRun(canvasId, {
        scope: current.scope, skip_succeeded: true, ...(current.kinds ? { kinds: current.kinds } : {}),
        client_request_id: current.requestId, quoted_revision: current.quote!.revision,
      })
      setBatch(started)
      setPlan(null)
      await refreshRuns()
      setPolling(true)
    } catch (reason) {
      if (reason instanceof ConflictError) {
        void loadQuote(current.scope, current.kinds, '画布在报价后被修改，已按最新内容重新报价，请确认后再运行')
        return
      }
      setPlan((value) => value ? { ...value, busy: false, error: reason instanceof Error ? reason.message : '提交失败' } : value)
    }
  }, [plan, canvasId, flushThen, refreshRuns, loadQuote])

  const cancelBatch = useCallback(async () => {
    if (!batch) return
    try {
      setBatch(await nodeCanvasApi.batchCancel(canvasId, batch.id))
      await refreshRuns()
    } catch (reason) { toast(reason instanceof Error ? reason.message : '取消失败', 'error') }
  }, [batch, canvasId, refreshRuns])

  // ── 预取预估价：保存完成、镜头或结果变化后（防抖）取 image / video 两档报价 ──
  const signature = runsSignature(runs)
  const hasNodes = graph.nodes.length > 0
  useEffect(() => {
    if (!loaded || !hasNodes || saveState !== 'saved') return
    let cancelled = false
    const timer = window.setTimeout(() => {
      const fetchOne = (kind: BatchKind) => nodeCanvasApi.batchQuote(canvasId, { scope: 'all', kinds: [kind] })
        .then((quote): Estimate => ({ state: 'ready', quote })).catch((): Estimate => ({ state: 'error', quote: null }))
      void Promise.all([fetchOne('image'), fetchOne('video')]).then(([image, video]) => { if (!cancelled) setEstimates({ image, video }) })
    }, ESTIMATE_DELAY)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [loaded, hasNodes, saveState, revision, signature, canvasId])

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

  /** 空状态“拆分镜头”：调用 Copilot 拆分（会调用语言模型），由调用方放进 graph。 */
  const split = useCallback((idea: string, shots: number) => nodeCanvasApi.split(canvasId, idea, shots), [canvasId])

  return useMemo(() => ({
    canvasId, loaded, error, title, aspectRatio, nodes: graph.nodes, edges: graph.edges, models, runs, saveState, revision, signature,
    batch, plan, setPlan, hintDismissed, setHintDismissed, estimates,
    update, updateData, flushThen, quote, run, split, openBatch, confirmBatch, cancelBatch, refreshRuns,
  }), [
    canvasId, loaded, error, title, aspectRatio, graph, models, runs, saveState, revision, signature, batch, plan, hintDismissed, estimates,
    update, updateData, flushThen, quote, run, split, openBatch, confirmBatch, cancelBatch, refreshRuns,
  ])
}

export type Storyboard = ReturnType<typeof useStoryboard>
