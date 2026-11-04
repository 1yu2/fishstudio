import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { finalApi, RenderBlockedError, type FinalSettings, type FinalState } from '../../api/final'
import { ConflictError, nodeCanvasApi, type StoredEdge, type StoredGraph, type StoredNode } from '../../api/nodeCanvas'
import { newRequestId } from '../nodeCanvas/graph'
import { toast } from '../ui/Toast'
import { DEFAULT_SETTINGS, renderActive } from './timelineLayout'

const SAVE_DELAY = 800
const POLL_INTERVAL = 2000
const IDLE_REFRESH_INTERVAL = 15000

export type SaveState = 'saved' | 'saving' | 'dirty' | 'error'
interface Graph { nodes: StoredNode[]; edges: StoredEdge[]; viewport?: { x: number; y: number; zoom: number } }

/**
 * 成片页的数据：成片接口（时间线 / 问题 / 渲染状态）+ 成片节点 data 的防抖保存（带 revision，409 时重新加载）。
 * 进入时如果画布里还没有成片节点，自动建一个。
 */
export function useFinal(canvasId: string, onRenderFinished?: () => void) {
  const [state, setState] = useState<FinalState | null>(null)
  const [settings, setSettings] = useState<FinalSettings>(DEFAULT_SETTINGS)
  const [bgmName, setBgmName] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [rendering, setRendering] = useState(false)
  const [renderError, setRenderError] = useState('')
  const [uploading, setUploading] = useState(false)

  const graphRef = useRef<Graph>({ nodes: [], edges: [] })
  const revisionRef = useRef(0)
  const nodeIdRef = useRef<string | null>(null)
  const dirty = useRef(false)
  const pending = useRef<Promise<void> | null>(null)
  const saveFailed = useRef(false)
  const wasActive = useRef(false)
  const finishedCb = useRef(onRenderFinished)
  finishedCb.current = onRenderFinished

  const refresh = useCallback(async () => {
    const next = await finalApi.get(canvasId)
    setState(next)
    nodeIdRef.current = next.episode_node_id
    // 本地有未保存的设置时不被服务端旧值覆盖
    if (!dirty.current && !pending.current) setSettings(next.settings)
    return next
  }, [canvasId])

  const applyDetail = useCallback((detail: { graph: StoredGraph; revision: number }) => {
    graphRef.current = { nodes: detail.graph.nodes || [], edges: detail.graph.edges || [], viewport: detail.graph.viewport }
    revisionRef.current = detail.revision
  }, [])

  const load = useCallback(async () => {
    let detail = await nodeCanvasApi.get(canvasId)
    applyDetail(detail)
    let next = await finalApi.get(canvasId)
    if (!next.episode_node_id) {
      const ensured = await finalApi.ensureNode(canvasId, detail.revision)
      detail = await nodeCanvasApi.get(canvasId)
      applyDetail(detail)
      next = await finalApi.get(canvasId)
      nodeIdRef.current = ensured.episode_node_id
    }
    nodeIdRef.current = next.episode_node_id
    setState(next)
    setSettings(next.settings)
    dirty.current = false
    setSaveState('saved')
  }, [canvasId, applyDetail])

  useEffect(() => {
    let cancelled = false
    setState(null); setError('')
    load().catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : '成片页加载失败') })
    return () => { cancelled = true }
  }, [load])

  // ── 保存成片设置（写回成片节点 data）──
  const save = useCallback(async () => {
    if (pending.current) await pending.current
    if (!dirty.current) return
    dirty.current = false
    saveFailed.current = false
    setSaveState('saving')
    const graph = graphRef.current
    pending.current = nodeCanvasApi.save(canvasId, { nodes: graph.nodes, edges: graph.edges, ...(graph.viewport ? { viewport: graph.viewport } : {}) }, revisionRef.current)
      .then((result) => { revisionRef.current = result.revision; setSaveState(dirty.current ? 'dirty' : 'saved') })
      .catch(async (reason) => {
        saveFailed.current = true
        setSaveState('error')
        if (reason instanceof ConflictError) {
          toast('画布在别处被修改，已刷新', 'error')
          try { await load() } catch { /* 刷新失败：保持错误状态 */ }
        } else {
          dirty.current = true
          toast(reason instanceof Error ? reason.message : '保存失败', 'error')
        }
      })
      .finally(() => { pending.current = null })
    await pending.current
    // 设置变了，成片的 input_hash / 过期状态也会变
    if (!saveFailed.current) await refresh().catch(() => undefined)
  }, [canvasId, load, refresh])

  const updateSettings = useCallback((patch: Partial<FinalSettings>) => {
    const nodeId = nodeIdRef.current
    if (!nodeId) return
    graphRef.current = {
      ...graphRef.current,
      nodes: graphRef.current.nodes.map((node) => node.id === nodeId ? { ...node, data: { ...node.data, ...patch } } : node),
    }
    setSettings((current) => ({ ...current, ...patch }))
    dirty.current = true
    setSaveState('dirty')
  }, [])

  useEffect(() => {
    if (saveState !== 'dirty') return
    const timer = window.setTimeout(() => void save(), SAVE_DELAY)
    return () => window.clearTimeout(timer)
  }, [saveState, settings, save])

  useEffect(() => () => { if (dirty.current) void save() }, [save])

  // 关闭 / 刷新：有未保存或保存中的设置时让浏览器确认；真正离开（pagehide）时用 keepalive 把最新 graph 发出去
  // （在途保存成功会让 revision +1，所以在途时带 revision + 1），与分镜页一致
  useEffect(() => {
    const unsaved = () => dirty.current || !!pending.current
    const confirmLeave = (event: BeforeUnloadEvent) => {
      if (!unsaved()) return
      event.preventDefault()
      event.returnValue = ''
    }
    const sendLatest = () => {
      if (!unsaved()) return
      const graph = graphRef.current
      nodeCanvasApi.saveKeepalive(canvasId, { nodes: graph.nodes, edges: graph.edges, ...(graph.viewport ? { viewport: graph.viewport } : {}) }, revisionRef.current + (pending.current ? 1 : 0))
    }
    window.addEventListener('beforeunload', confirmLeave)
    window.addEventListener('pagehide', sendLatest)
    return () => {
      window.removeEventListener('beforeunload', confirmLeave)
      window.removeEventListener('pagehide', sendLatest)
    }
  }, [canvasId])

  /** 先把未保存的设置写到服务端；失败就抛错，调用方据此中止。 */
  const flush = useCallback(async () => {
    if (!dirty.current && !pending.current) return
    await save()
    if (saveFailed.current || dirty.current) throw new Error('成片设置保存失败，已取消操作，请稍后重试')
  }, [save])

  // ── 渲染进度：渲染中每 2 秒刷新，页面隐藏时暂停；结束时通知项目页刷新步骤状态 ──
  const active = renderActive(state)
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => { if (!document.hidden) void refresh().catch(() => undefined) }, POLL_INTERVAL)
    return () => window.clearInterval(timer)
  }, [active, refresh])
  // 没有渲染在跑时也低频刷新（素材在别处变化会让“已过期”状态变化）：每 15 秒一次，窗口重新可见时立即一次
  useEffect(() => {
    if (active) return
    const tick = () => { if (!document.hidden) void refresh().catch(() => undefined) }
    const timer = window.setInterval(tick, IDLE_REFRESH_INTERVAL)
    document.addEventListener('visibilitychange', tick)
    window.addEventListener('focus', tick)
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', tick); window.removeEventListener('focus', tick) }
  }, [active, refresh])
  useEffect(() => {
    if (wasActive.current && !active) finishedCb.current?.()
    wasActive.current = active
  }, [active])

  const render = useCallback(async () => {
    if (rendering) return
    setRendering(true); setRenderError('')
    try {
      await flush()
      const body = { base_revision: revisionRef.current, client_request_id: newRequestId() }
      try { await finalApi.render(canvasId, body) }
      catch (reason) {
        if (!(reason instanceof TypeError)) throw reason
        await finalApi.render(canvasId, body)  // 网络层失败：同一个标识重试，后端不会重复建任务
      }
      await refresh()
    } catch (reason) {
      if (reason instanceof RenderBlockedError) { setRenderError(reason.message); await refresh().catch(() => undefined) }
      else if (reason instanceof ConflictError) { toast(reason.message, 'error'); await load().catch(() => undefined) }
      else { const message = reason instanceof Error ? reason.message : '提交失败'; setRenderError(message); toast(message, 'error') }
    } finally { setRendering(false) }
  }, [rendering, flush, canvasId, refresh, load])

  const uploadBgm = useCallback(async (file: File) => {
    setUploading(true)
    try {
      const uploaded = await finalApi.uploadBgm(canvasId, file)
      setBgmName(uploaded.name)
      updateSettings({ bgm_artifact_id: uploaded.artifact_id })
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : '上传失败', 'error')
    } finally { setUploading(false) }
  }, [canvasId, updateSettings])

  const removeBgm = useCallback(() => { setBgmName(null); updateSettings({ bgm_artifact_id: null }) }, [updateSettings])

  return useMemo(() => ({
    state, settings, error, saveState, rendering, renderError, uploading,
    bgmName: settings.bgm_artifact_id ? (bgmName || state?.timeline.bgm?.name || '背景音乐') : null,
    updateSettings, render, uploadBgm, removeBgm, refresh, flush,
  }), [state, settings, error, saveState, rendering, renderError, uploading, bgmName, updateSettings, render, uploadBgm, removeBgm, refresh, flush])
}

export type FinalData = ReturnType<typeof useFinal>
