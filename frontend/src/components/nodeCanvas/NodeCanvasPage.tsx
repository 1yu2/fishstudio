import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  Background, BackgroundVariant, ControlButton, Controls, MiniMap, Panel, ReactFlow, ReactFlowProvider, addEdge,
  useEdgesState, useNodesState, useReactFlow, type Connection, type Edge, type Node, type NodeChange, type OnBeforeDelete, type OnConnectEnd,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Check, ChevronDown, ChevronsDownUp, ChevronsUpDown, Film, ImageIcon, LayoutGrid, Loader2, Maximize, Mic, MoreHorizontal, Play, Plus, Sparkles, Type, X } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  ConflictError, nodeCanvasApi, type BatchRun, type BatchRunKind, type BatchScope, type CanvasDetail, type CanvasModels, type CanvasNodeType, type CanvasRuns, type RunRequest,
  type SplitResult, type StoredEdge, type StoredGraph, type StoredNode,
} from '../../api/nodeCanvas'
import { paths } from '../../routePaths'
import { toast } from '../ui/Toast'
import { CanvasContext, type CanvasActions } from './context'
import { isCharacterNode } from '../characters/characters'
import { buildShots, newShot } from '../storyboard/shots'
import { canConnect, defaultData, dropTarget, edgeId, layoutSplit, newId, newRequestId } from './graph'
import {
  CARD_SIZE, GROUP_HEADER, GROUP_NODE_TYPE, GROUP_PADDING, buildGroups, contentBottom, defaultCollapsed, expandWithRoom, focusViewport, groupNodeId, loadCollapsed, nodeRect, saveCollapsed, tidyLayout, unionRect, withCachedSizes,
  type Rect, type ShotGroup, type SizeCache, type SizedNode,
} from './groups'
import type { ShotGroupData } from './ShotGroupNode'
import { BatchDialog, kindsLabel, type BatchPlan } from './BatchDialog'
import { nodeTypes } from './nodes'
import './nodeCanvas.css'

type SaveState = 'saved' | 'saving' | 'dirty' | 'error'
const SAVE_DELAY = 800
const POLL_INTERVAL = 3000
/** 量不到工具栏时（首次渲染前 / 测试环境）使用的避让高度 */
const FALLBACK_TOOLBAR_HEIGHT = 72
/** 初次进入时工具栏还没渲染，fitView 先按单行工具栏的高度留顶部空间 */
const INITIAL_FIT_PADDING = { top: '96px', right: '48px', bottom: '48px', left: '48px' } as const
/** 新镜头放在现有内容下方时与它们的间距 */
const SHOT_GAP = 48

/** 分组节点是派生的，任何来源的 graph 里出现都丢掉 */
const isDerived = (node: { type?: string }) => node.type === GROUP_NODE_TYPE

function toFlow(graph: StoredGraph): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: (graph.nodes || []).filter((node) => !isDerived(node)).map((node) => ({ id: node.id, type: node.type, position: node.position, data: node.data })),
    edges: (graph.edges || []).map((edge) => ({ ...edge, sourceHandle: edge.sourceHandle ?? 'out' })),
  }
}

/** 只保存节点的真实内容：不含派生的分组节点，也不含 hidden / measured 这类显示状态。 */
function stored(nodes: Node[]): StoredNode[] {
  return nodes.filter((node) => !isDerived(node))
    .map((node) => ({ id: node.id, type: node.type as CanvasNodeType, position: node.position, data: node.data as Record<string, unknown> }))
}

/** 会被保存的内容的指纹 */
function graphSignature(nodes: Node[], edges: Edge[]): string {
  return JSON.stringify([stored(nodes), storedEdges(edges)])
}

/** 供分组计算用：带测量尺寸的节点 */
function sized(nodes: Node[]): SizedNode[] {
  return nodes.filter((node) => !isDerived(node)).map((node) => ({
    id: node.id, type: node.type as CanvasNodeType, position: node.position, data: node.data as Record<string, unknown>, measured: node.measured,
  }))
}

function storedEdges(edges: Edge[]): StoredEdge[] {
  return edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target, sourceHandle: edge.sourceHandle ?? null, targetHandle: edge.targetHandle ?? null }))
}

/** 工具栏上的下拉菜单：打开后焦点进第一项，上下键移动，Esc / 执行菜单项后焦点还给触发按钮，点外面关闭。 */
function ToolbarMenu({ label, icon, disabled, children }: { label: string; icon: ReactNode; disabled?: boolean; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const items = () => [...(ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') || [])]
  const close = useCallback(() => { setOpen(false); trigger.current?.focus() }, [])
  useEffect(() => {
    if (!open) return
    items()[0]?.focus()
    const onDown = (event: MouseEvent) => { if (!(event.target instanceof Element) || !ref.current?.contains(event.target)) setOpen(false) }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!open) return
    if (event.key === 'Escape') { event.preventDefault(); close(); return }
    if (event.key === 'Tab') { setOpen(false); return }
    const move = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0
    const edge = event.key === 'Home' ? 0 : event.key === 'End' ? -1 : null
    if (!move && edge === null) return
    event.preventDefault()
    const list = items()
    if (list.length === 0) return
    const at = list.indexOf(document.activeElement as HTMLElement)
    const next = edge !== null ? (edge === 0 ? 0 : list.length - 1) : (at + move + list.length) % list.length
    list[next].focus()
  }
  return <div className="nc-menu" ref={ref} onKeyDown={onKeyDown}>
    <button ref={trigger} className="nc-tool" aria-haspopup="menu" aria-expanded={open} disabled={disabled} onClick={() => setOpen((value) => !value)}>
      {icon}{label}<ChevronDown size={12} />
    </button>
    {open && <div className="nc-menu__list" role="menu">{children(close)}</div>}
  </div>
}

function themeMode(): 'dark' | 'light' {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

function CopilotPanel({ canvasId, onPlace, onClose }: { canvasId: string; onPlace: (split: SplitResult) => void; onClose: () => void }) {
  const [idea, setIdea] = useState('')
  const [shots, setShots] = useState(4)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<SplitResult | null>(null)
  const [error, setError] = useState('')
  const split = async () => {
    setBusy(true); setError('')
    try { setResult(await nodeCanvasApi.split(canvasId, idea.trim(), shots)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '拆分失败') }
    finally { setBusy(false) }
  }
  return <aside className="nc-copilot" aria-label="Copilot">
    <header><Sparkles size={15} /><strong>Copilot</strong><button className="nc-icon" aria-label="关闭 Copilot" onClick={onClose}><X size={14} /></button></header>
    <p className="nc-copilot__hint">把一个短片想法拆成角色和镜头，放到画布上：角色定妆图 → 镜头关键帧 → 视频。</p>
    <textarea className="nc-textarea" aria-label="想法" rows={6} value={idea} placeholder="例如：雨夜便利店，女店员发现一只湿透的小猫……" onChange={(event) => setIdea(event.target.value)} />
    <label className="nc-copilot__shots">镜头数<input type="number" min={1} max={12} value={shots} onChange={(event) => setShots(Math.max(1, Math.min(12, Number(event.target.value) || 1)))} /></label>
    <button className="nc-run nc-run--wide" disabled={busy || !idea.trim()} onClick={() => void split()}>{busy ? <Loader2 size={13} className="nc-spin" /> : <Sparkles size={13} />}拆分镜头</button>
    {error && <div className="nc-error nc-copilot__error" role="alert">{error}</div>}
    {result && <div className="nc-copilot__result">
      {result.characters.length > 0 && <section><h4>角色</h4>{result.characters.map((item) => <div key={item.name} className="nc-copilot__item"><b>{item.name}</b>{item.description}</div>)}</section>}
      <section><h4>镜头</h4>{result.shots.map((shot, index) => <div key={index} className="nc-copilot__item"><b>{index + 1}. {shot.title || '镜头'} · {shot.duration_s}s</b>{shot.prompt}</div>)}</section>
      <button className="nc-run nc-run--wide" onClick={() => { onPlace(result); setResult(null) }}>放到画布</button>
    </div>}
  </aside>
}

function Canvas({ detail, models }: { detail: CanvasDetail; models: CanvasModels | null }) {
  const initial = useMemo(() => toFlow(detail.graph), [detail])
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initial.nodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial.edges)
  const [runs, setRuns] = useState<CanvasRuns | null>(null)
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [copilot, setCopilot] = useState(false)
  const [polling, setPolling] = useState(true)
  const [batch, setBatch] = useState<BatchRun | null>(null)
  const [plan, setPlan] = useState<BatchPlan | null>(null)
  const [hintDismissed, setHintDismissed] = useState<string | null>(null)  // 已关闭提示的批次 id；新批次 id 不同，提示自然不再显示
  const revision = useRef(detail.revision)
  const latest = useRef({ nodes, edges })
  const pending = useRef<Promise<void> | null>(null)
  const dirty = useRef(false)
  const saveFailed = useRef(false)
  const loaded = useRef(false)
  const flow = useReactFlow()
  latest.current = { nodes, edges }
  const flowRef = useRef<HTMLDivElement>(null)
  const topRef = useRef<HTMLDivElement>(null)

  // 分镜页“在画布中打开”带 ?node=<id>：进入后展开该节点所在的镜头组、选中它并把视口对到它
  const [searchParams] = useSearchParams()
  const focusNode = searchParams.get('node')

  // 镜头分组：由 graph 推导；折叠状态存 localStorage（没存过按镜头数给默认值）
  // 记住每个节点最后一次测到的尺寸：折叠后节点被隐藏、不再测量，展开 / 整理时仍按它算
  const sizeCache = useRef<SizeCache>(new Map())
  const flowNodes = useMemo(() => {
    for (const node of nodes) {
      const { width, height } = node.measured || {}
      if (width && height) sizeCache.current.set(node.id, { width, height })
    }
    return withCachedSizes(sized(nodes), sizeCache.current)
  }, [nodes])
  const sizedNow = useCallback((): SizedNode[] => withCachedSizes(sized(latest.current.nodes), sizeCache.current), [])
  // 刚展开、等成员测量完要再校正一次让位的组
  const pendingFix = useRef(new Set<string>())
  const flowEdges = useMemo(() => storedEdges(edges), [edges])
  const groups = useMemo(() => buildGroups(flowNodes, flowEdges, runs), [flowNodes, flowEdges, runs])
  const groupsRef = useRef(groups)
  groupsRef.current = groups
  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    const first = buildGroups(sized(initial.nodes), storedEdges(initial.edges), null)
    const value = new Set(loadCollapsed(detail.id) ?? defaultCollapsed(first.map((group) => group.shotId)))
    return value
  })
  const updateCollapsed = useCallback((change: (value: Set<string>) => Set<string>) => {
    setCollapsed((current) => { const next = change(current); saveCollapsed(detail.id, next); return next })
  }, [detail.id])
  const collapsedRef = useRef(collapsed)
  collapsedRef.current = collapsed
  const runsRef = useRef(runs)
  runsRef.current = runs
  // 展开若干组（按顺序依次让位）：被展开后的框盖住的内容往下推并保存新位置；折叠不回收空间
  const expandGroups = useCallback((ids: string[], room = true) => {
    for (const id of ids) pendingFix.current.add(id)
    // room=false（?node= 打开）：只展开，不在这一刻用可能是默认的尺寸去推别人，等成员测量完由校正来做
    const result = room
      ? expandWithRoom(sizedNow(), storedEdges(latest.current.edges), runsRef.current, collapsedRef.current, ids)
      : { positions: {} as Record<string, { x: number; y: number }>, collapsed: new Set([...collapsedRef.current].filter((id) => !ids.includes(id))) }
    const { positions } = result
    if (Object.keys(positions).length > 0) setNodes((items) => items.map((node) => positions[node.id] ? { ...node, position: positions[node.id] } : node))
    updateCollapsed(() => result.collapsed)
  }, [setNodes, updateCollapsed, sizedNow])
  const toggleGroup = useCallback((shotId: string) => {
    if (collapsedRef.current.has(shotId)) expandGroups([shotId])
    else { pendingFix.current.delete(shotId); updateCollapsed((current) => new Set(current).add(shotId)) }
  }, [expandGroups, updateCollapsed])
  const allCollapsed = groups.length > 0 && groups.every((group) => collapsed.has(group.shotId))
  const toggleAll = () => {
    if (allCollapsed) expandGroups(groups.map((group) => group.shotId))
    else { pendingFix.current.clear(); updateCollapsed(() => new Set(groups.map((group) => group.shotId))) }
  }

  // 工具栏占掉的顶部高度（相对画布容器）；量不到时用回退值
  const toolbarInset = useCallback(() => {
    const box = flowRef.current?.getBoundingClientRect()
    const bar = topRef.current?.getBoundingClientRect()
    return box && bar && bar.height > 0 ? bar.bottom - box.top + 12 : FALLBACK_TOOLBAR_HEIGHT
  }, [])
  // 把一块区域对到视口中央，并让出顶部工具栏的高度，避免被它挡住
  const focusRect = useCallback((rect: Rect, duration = 400) => {
    const box = flowRef.current?.getBoundingClientRect()
    const size = box && box.width > 0 && box.height > 0 ? { width: box.width, height: box.height } : { width: window.innerWidth, height: window.innerHeight }
    void Promise.resolve(flow.setViewport(focusViewport(rect, size, toolbarInset()), { duration })).catch(() => undefined)
  }, [flow, toolbarInset])
  // 适应画面：所有 fitView 都走这里，顶部让出工具栏高度
  const fitAll = useCallback((duration = 300) => {
    void Promise.resolve(flow.fitView({ padding: { top: `${toolbarInset() + 16}px` as const, right: '48px', bottom: '48px', left: '48px' }, maxZoom: 1, duration })).catch(() => undefined)
  }, [flow, toolbarInset])
  // 定位到一个节点：在组里就对整组，否则对节点自己。等节点测量完再定位，所以延后一点
  const focusNodeLater = useCallback((nodeId: string) => {
    return window.setTimeout(() => {
      const group = groupsRef.current.find((item) => item.members.includes(nodeId))
      const node = latest.current.nodes.find((item) => item.id === nodeId)
      if (group) focusRect(group.bounds)
      else if (node) focusRect(nodeRect(sized([node])[0]))
    }, 80)
  }, [focusRect])

  useEffect(() => {
    if (!focusNode || !latest.current.nodes.some((node) => node.id === focusNode)) return
    const group = groupsRef.current.find((item) => item.members.includes(focusNode))
    // 目标组本来就展开：只定位，不改任何坐标；原来折叠的：只展开，让位交给测量后的校正
    if (group && collapsedRef.current.has(group.shotId)) expandGroups([group.shotId], false)
    setNodes((items) => items.map((node) => ({ ...node, selected: node.id === focusNode })))
    const timer = focusNodeLater(focusNode)
    return () => window.clearTimeout(timer)
  }, [focusNode, focusNodeLater, setNodes, expandGroups])

  const save = useCallback(async () => {
    if (pending.current) await pending.current
    if (!dirty.current) return
    dirty.current = false
    saveFailed.current = false
    setSaveState('saving')
    const graph: StoredGraph = { nodes: stored(latest.current.nodes), edges: storedEdges(latest.current.edges), viewport: flow.getViewport() }
    const signature = graphSignature(latest.current.nodes, latest.current.edges)
    pending.current = nodeCanvasApi.save(detail.id, graph, revision.current)
      .then((result) => { revision.current = result.revision; savedSignature.current = signature; setSaveState(dirty.current ? 'dirty' : 'saved') })
      .catch(async (error) => {
        saveFailed.current = true
        setSaveState('error')
        if (error instanceof ConflictError) {
          toast('画布在别处被修改，已加载最新版本', 'error')
          const fresh = await nodeCanvasApi.get(detail.id)
          const next = toFlow(fresh.graph)
          revision.current = fresh.revision
          savedSignature.current = graphSignature(next.nodes, next.edges)
          setNodes(next.nodes); setEdges(next.edges)
          setSaveState('saved')
        } else {
          dirty.current = true
          toast(error instanceof Error ? error.message : '自动保存失败', 'error')
        }
      })
      .finally(() => { pending.current = null })
    await pending.current
  }, [detail.id, flow, setEdges, setNodes])

  const markDirty = useCallback(() => { dirty.current = true; setSaveState('dirty') }, [])

  // 只有真正会保存的内容（节点 / 边）变了才算脏：选中、测量尺寸、折叠这类显示状态变化不触发保存
  const savedSignature = useRef(graphSignature(initial.nodes, initial.edges))
  useEffect(() => {
    if (!loaded.current) { loaded.current = true; return }
    if (graphSignature(nodes, edges) === savedSignature.current) return
    markDirty()
  }, [nodes, edges, markDirty])

  useEffect(() => {
    if (saveState !== 'dirty') return
    const timer = window.setTimeout(() => void save(), SAVE_DELAY)
    return () => window.clearTimeout(timer)
  }, [saveState, nodes, edges, save])

  useEffect(() => {
    const flush = () => { if (dirty.current) void save() }
    window.addEventListener('beforeunload', flush)
    return () => { window.removeEventListener('beforeunload', flush); flush() }
  }, [save])

  const refreshRuns = useCallback(async () => {
    try {
      const value = await nodeCanvasApi.runs(detail.id)
      setRuns(value)
      setPolling(value.active_jobs > 0)
    } catch { /* 下次轮询再试 */ }
  }, [detail.id])

  useEffect(() => { void refreshRuns() }, [refreshRuns])
  useEffect(() => {
    if (!polling) return
    const timer = window.setInterval(() => void refreshRuns(), POLL_INTERVAL)
    return () => window.clearInterval(timer)
  }, [polling, refreshRuns])

  // 打开画布时接上还在跑的批量运行（服务端推进，不依赖浏览器开着）
  useEffect(() => {
    void (async () => {
      try {
        const [latest] = await nodeCanvasApi.batchList(detail.id)
        if (latest) setBatch(latest)  // 任何终态都恢复：画面批次完成的提示重开后还在；只有 running 才会轮询
      } catch { /* 没有也不影响使用 */ }
    })()
  }, [detail.id])

  const batchId = batch?.status === 'running' ? batch.id : null
  useEffect(() => {
    if (!batchId) return
    const controller = new AbortController()
    const timer = window.setInterval(() => {
      void nodeCanvasApi.batchGet(detail.id, batchId, controller.signal).then((value) => {
        // 慢响应可能晚于取消 / 新批次到达：只接受仍是当前批次的结果
        if (controller.signal.aborted) return
        setBatch((current) => current && current.id === value.id ? value : current)
        void refreshRuns()
      }).catch(() => undefined)
    }, POLL_INTERVAL)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [batchId, detail.id, refreshRuns])

  const updateData = useCallback((nodeId: string, patch: Record<string, unknown>) => {
    setNodes((items) => items.map((node) => node.id === nodeId ? { ...node, data: { ...node.data, ...patch } } : node))
  }, [setNodes])

  // 先把未保存的改动写到服务端；保存失败（含版本冲突）就抛错，调用方据此中止，避免按旧图运行或报价
  const flushThen = useCallback(async () => {
    if (!dirty.current && !pending.current) return
    await save()
    if (saveFailed.current || dirty.current) throw new Error('画布保存失败，已取消操作，请稍后重试')
  }, [save])

  // 报价：先保存画布再报价；notice 用于说明为什么重新报价。每次报价换一个 requestId（新的决定）
  const loadQuote = useCallback(async (scope: BatchScope, nodeId?: string, notice = '', kinds?: BatchRunKind[]) => {
    const next: BatchPlan = { scope, nodeId, kinds, requestId: newRequestId(), quote: null, error: '', notice, busy: false }
    setPlan(next)
    const apply = (patch: Partial<BatchPlan>) => setPlan((current) => current?.requestId === next.requestId ? { ...current, ...patch } : current)
    try {
      await flushThen()
      apply({ quote: await nodeCanvasApi.batchQuote(detail.id, { scope, node_id: nodeId, ...(kinds ? { kinds } : {}) }) })
    } catch (error) {
      apply({ error: error instanceof Error ? error.message : '报价失败' })
    }
  }, [detail.id, flushThen])

  const openBatch = useCallback((scope: BatchScope, nodeId?: string, kinds?: BatchRunKind[]) => loadQuote(scope, nodeId, '', kinds), [loadQuote])

  const confirmBatch = async () => {
    if (!plan || plan.busy || !plan.quote) return
    const current = plan
    setPlan({ ...current, busy: true, error: '' })
    try {
      await flushThen()
      // 同一个确认窗口内重复点击 / 重试都复用 requestId，后端返回同一个批次
      const started = await nodeCanvasApi.batchRun(detail.id, {
        scope: current.scope, node_id: current.nodeId, skip_succeeded: true, ...(current.kinds ? { kinds: current.kinds } : {}),
        client_request_id: current.requestId, quoted_revision: current.quote!.revision,
      })
      setBatch(started)
      setPlan(null)
      await refreshRuns()
      setPolling(true)
    } catch (error) {
      if (error instanceof ConflictError) {
        // 报价后画布又被改过：价格已不适用，按最新内容重新报价并再次确认
        void loadQuote(current.scope, current.nodeId, '画布在报价后被修改，已按最新内容重新报价，请确认后再运行', current.kinds)
        return
      }
      setPlan((value) => value ? { ...value, busy: false, error: error instanceof Error ? error.message : '提交失败' } : value)
    }
  }

  const cancelBatch = async () => {
    if (!batch) return
    try {
      setBatch(await nodeCanvasApi.batchCancel(detail.id, batch.id))
      await refreshRuns()
    } catch (error) { toast(error instanceof Error ? error.message : '取消失败', 'error') }
  }

  const storedNodes = useMemo(() => stored(nodes), [nodes])
  const characterIds = useMemo(() => new Set(storedNodes.filter((node) => isCharacterNode(node, flowEdges, storedNodes)).map((node) => node.id)), [storedNodes, flowEdges])
  const hasCharacterRefs = useMemo(() => flowEdges.some((edge) => edge.targetHandle === 'reference' && characterIds.has(edge.source)), [flowEdges, characterIds])

  const actions: CanvasActions = useMemo(() => ({
    canvasId: detail.id,
    aspectRatio: detail.aspect_ratio,
    models,
    runs,
    nodes: storedNodes,
    edges: flowEdges,
    characterIds,
    updateData,
    quote: (request: RunRequest) => nodeCanvasApi.quote(detail.id, request),
    runBatch: (scope: BatchScope, nodeId?: string) => { void openBatch(scope, nodeId) },
    run: async (nodeId: string, request: RunRequest) => {
      try {
        await flushThen()
        // 每次点击一个新的 client_request_id；网络层失败的自动重试复用它，后端不会重复建任务
        const body = { ...request, client_request_id: newRequestId() }
        try { await nodeCanvasApi.run(detail.id, nodeId, body) }
        catch (error) {
          if (!(error instanceof TypeError)) throw error
          await nodeCanvasApi.run(detail.id, nodeId, body)
        }
        await refreshRuns()
        setPolling(true)
      } catch (error) { toast(error instanceof Error ? error.message : '提交失败', 'error') }
    },
    cancel: async (jobId: string) => {
      try {
        await nodeCanvasApi.cancelJob(jobId)
        await refreshRuns()
      } catch (error) { toast(error instanceof Error ? error.message : '取消失败', 'error') }
    },
    upload: async (nodeId: string, file: File) => {
      try {
        await flushThen()
        const result = await nodeCanvasApi.upload(detail.id, nodeId, file)
        updateData(nodeId, { selected: result.artifact_id })
        await refreshRuns()
      } catch (error) { toast(error instanceof Error ? error.message : '上传失败', 'error') }
    },
  }), [detail.id, detail.aspect_ratio, models, runs, storedNodes, flowEdges, characterIds, updateData, flushThen, refreshRuns, openBatch])

  const isValidConnection = useCallback((connection: Connection | Edge) => canConnect(stored(latest.current.nodes), storedEdges(latest.current.edges), connection), [])

  const onConnect = useCallback((connection: Connection) => {
    if (!canConnect(stored(latest.current.nodes), storedEdges(latest.current.edges), connection)) return
    setEdges((items) => addEdge({ ...connection, id: edgeId(connection.source, connection.target, connection.targetHandle || '') }, items))
  }, [setEdges])

  const addNode = useCallback((type: CanvasNodeType, position?: { x: number; y: number }, data?: Record<string, unknown>) => {
    const id = newId(type)
    const at = position || flow.screenToFlowPosition({ x: window.innerWidth / 2 - 140, y: window.innerHeight / 2 - 120 })
    setNodes((items) => [...items.map((node) => ({ ...node, selected: false })), { id, type, position: at, data: data || defaultData(type), selected: true }])
    return id
  }, [flow, setNodes])

  const onConnectEnd: OnConnectEnd = useCallback((event, state) => {
    if (state.isValid || !state.fromNode || !state.fromHandle) return
    const fromType = state.fromNode.type as CanvasNodeType
    const target = dropTarget(fromType, state.fromHandle.type, state.fromHandle.id ?? null)
    if (!target) return
    const point = 'changedTouches' in event ? event.changedTouches[0] : event
    const position = flow.screenToFlowPosition({ x: point.clientX, y: point.clientY })
    const id = addNode(target.type, { x: position.x - (target.reverse ? 280 : 0), y: position.y - 40 })
    const fromId = state.fromNode.id
    const [source, dest] = target.reverse ? [id, fromId] : [fromId, id]
    setEdges((items) => [...items, { id: edgeId(source, dest, target.handle), source, target: dest, sourceHandle: 'out', targetHandle: target.handle }])
  }, [addNode, flow, setEdges])

  const placeSplit = useCallback((split: SplitResult) => {
    const laid = layoutSplit(split, stored(latest.current.nodes))
    setNodes((items) => [...items, ...toFlow({ nodes: laid.nodes }).nodes])
    setEdges((items) => [...items, ...toFlow({ edges: laid.edges }).edges])
    const area = unionRect(laid.nodes.map((node) => nodeRect(node)))
    if (area) window.setTimeout(() => focusRect(area), 50)
  }, [focusRect, setEdges, setNodes])

  // 在已有镜头下方新建一组镜头节点（口径同分镜页的“添加镜头”），默认展开并定位
  const addShot = useCallback(() => {
    const current = stored(latest.current.nodes)
    const made = newShot(current, buildShots(current, storedEdges(latest.current.edges), runs))
    // 数据口径沿用 newShot，位置改放到所有节点和组框（用实测尺寸）的下方，留出新组的标题和间距
    const bottom = contentBottom(sizedNow(), groupsRef.current, collapsedRef.current)
    if (bottom !== null) {
      const y = bottom + SHOT_GAP + GROUP_PADDING + GROUP_HEADER
      for (const item of made.nodes) item.position = { ...item.position, y }
    }
    setNodes((items) => [
      ...items.map((node) => ({ ...node, selected: false, ...(node.id in made.orders ? { data: { ...node.data, order: made.orders[node.id] } } : {}) })),
      ...toFlow({ nodes: made.nodes }).nodes.map((node) => ({ ...node, selected: node.id === made.mainId })),
    ])
    setEdges((items) => [...items, ...toFlow({ edges: made.edges }).edges])
    updateCollapsed((value) => { const next = new Set(value); next.delete(made.mainId); return next })
    const area = unionRect(made.nodes.map((node) => nodeRect(node)))
    window.setTimeout(() => {
      const group = groupsRef.current.find((item) => item.id === groupNodeId(made.mainId))
      if (group) focusRect(group.bounds)
      else if (area) focusRect(area)
    }, 80)
  }, [focusRect, runs, setEdges, setNodes, updateCollapsed])

  // 渲染用的节点：成员 + 派生的分组节点。折叠时成员只是 hidden（显示状态，不进 state、不保存）
  const hiddenIds = useMemo(() => new Set(groups.filter((group) => collapsed.has(group.shotId)).flatMap((group) => group.members)), [groups, collapsed])
  // 刚展开的组：只要收到它的成员“展开之后”的尺寸变化（measured 变了），就按镜头顺序再让位一次。
  // 以尺寸变化为准，所以折叠前留下的旧 measured 不会让校正提前结束；展开时尺寸没变就无事可做，标记保留，等真有变化再校正。
  // 校正只改 position、不改 measured，不会循环；一个组校正过一次就清除标记。
  const lastMeasured = useRef(new Map<string, string>())
  useEffect(() => {
    const changed = new Set<string>()
    for (const node of nodes) {
      const { width, height } = node.measured || {}
      if (!width || !height) continue
      const key = `${width}x${height}`
      if (lastMeasured.current.get(node.id) !== key) { lastMeasured.current.set(node.id, key); changed.add(node.id) }
    }
    if (pendingFix.current.size === 0 || changed.size === 0) return
    const ready = groupsRef.current
      .filter((group) => pendingFix.current.has(group.shotId) && !collapsedRef.current.has(group.shotId) && group.members.some((id) => changed.has(id)))
      .map((group) => group.shotId)
    if (ready.length === 0) return
    for (const id of ready) pendingFix.current.delete(id)
    const { positions } = expandWithRoom(sizedNow(), storedEdges(latest.current.edges), runsRef.current, collapsedRef.current, ready)
    if (Object.keys(positions).length > 0) setNodes((items) => items.map((node) => positions[node.id] ? { ...node, position: positions[node.id] } : node))
  }, [nodes, setNodes, sizedNow])

  const hiddenRef = useRef(hiddenIds)
  hiddenRef.current = hiddenIds
  // 折叠后看不见的节点和边不能还处于选中状态，否则按 Delete 会误删
  useEffect(() => {
    if (hiddenIds.size === 0) return
    const hiddenEdge = (edge: Edge) => hiddenIds.has(edge.source) || hiddenIds.has(edge.target)
    if (latest.current.nodes.some((node) => node.selected && hiddenIds.has(node.id))) {
      setNodes((items) => items.map((node) => node.selected && hiddenIds.has(node.id) ? { ...node, selected: false } : node))
    }
    if (latest.current.edges.some((edge) => edge.selected && hiddenEdge(edge))) {
      setEdges((items) => items.map((edge) => edge.selected && hiddenEdge(edge) ? { ...edge, selected: false } : edge))
    }
  }, [hiddenIds, setEdges, setNodes])
  // 删除前再拦一道：隐藏的节点、派生的分组节点不能被删；连着隐藏节点的边只有在另一端节点确实被删时才跟着删
  const onBeforeDelete: OnBeforeDelete<Node, Edge> = useCallback(async ({ nodes: doomed, edges: doomedEdges }) => {
    const hidden = hiddenRef.current
    const kept = doomed.filter((node) => !hidden.has(node.id) && node.type !== GROUP_NODE_TYPE)
    const gone = new Set(kept.map((node) => node.id))
    return {
      nodes: kept,
      // 一端是实际被删的节点的边一律跟着删（哪怕另一端藏着），否则会留下指向已删节点的残边；其余连着隐藏节点的边不能删
      edges: doomedEdges.filter((edge) => gone.has(edge.source) || gone.has(edge.target) || (!hidden.has(edge.source) && !hidden.has(edge.target))),
    }
  }, [])

  const tidy = useCallback(() => {
    const positions = tidyLayout(sizedNow(), storedEdges(latest.current.edges), runs, collapsedRef.current)
    setNodes((items) => items.map((node) => positions[node.id] ? { ...node, position: positions[node.id] } : node))
    window.setTimeout(() => fitAll(), 80)
  }, [fitAll, runs, setNodes])

  const displayNodes = useMemo(() => {
    const derived: Node[] = groups.map((group: ShotGroup) => {
      const folded = collapsed.has(group.shotId)
      const data: ShotGroupData = { group, collapsed: folded, onToggle: toggleGroup }
      return {
        id: group.id, type: GROUP_NODE_TYPE, position: { x: group.bounds.x, y: group.bounds.y }, data,
        style: folded ? { width: CARD_SIZE.width, height: CARD_SIZE.height } : { width: group.bounds.width, height: group.bounds.height },
        // 展开的框垫在成员下面；只有标题栏（展开）/ 整张卡片（折叠）能拖
        zIndex: folded ? 1 : -1, dragHandle: folded ? '.nc-group-card' : '.nc-group__head',
        selectable: false, deletable: false, connectable: false, focusable: false,
      }
    })
    const members = hiddenIds.size ? nodes.map((node) => hiddenIds.has(node.id) ? { ...node, hidden: true } : node) : nodes
    return [...derived, ...members]
  }, [groups, collapsed, hiddenIds, nodes, toggleGroup])
  // 角色参考线画成虚线；只是显示样式，不写进 edges state
  const displayEdges = useMemo(() => edges.map((edge) => characterIds.has(edge.source) && edge.targetHandle === 'reference'
    ? { ...edge, style: { ...edge.style, strokeDasharray: '6 4', stroke: 'var(--accent)', opacity: 0.6 } } : edge), [edges, characterIds])

  // 拖动分组标题栏 = 整组成员一起移动：把分组节点的位置变化换算成成员的位置变化
  const handleNodesChange = useCallback((changes: NodeChange<Node>[]) => {
    const byId = new Map(groupsRef.current.map((group) => [group.id, group]))
    const passed: NodeChange<Node>[] = []
    for (const change of changes) {
      const group = 'id' in change ? byId.get(change.id) : undefined
      if (!group) { passed.push(change); continue }
      if (change.type !== 'position' || !change.position) continue
      const dx = change.position.x - group.bounds.x
      const dy = change.position.y - group.bounds.y
      if (!dx && !dy && change.dragging !== false) continue
      for (const id of group.members) {
        const member = latest.current.nodes.find((node) => node.id === id)
        if (member) passed.push({ type: 'position', id, dragging: change.dragging, position: { x: member.position.x + dx, y: member.position.y + dy } })
      }
    }
    if (passed.length) onNodesChange(passed)
  }, [onNodesChange])

  const saveLabel = { saved: '已保存', saving: '保存中…', dirty: '未保存', error: '保存失败' }[saveState]

  const running = batch?.status === 'running'

  return <CanvasContext.Provider value={actions}>
    <div className={`nc-page${copilot ? ' nc-page--copilot' : ''}`}>
      <div className="nc-flow" ref={flowRef}>
        <ReactFlow nodes={displayNodes} edges={displayEdges} nodeTypes={nodeTypes} onNodesChange={handleNodesChange} onEdgesChange={onEdgesChange}
          onConnect={onConnect} onConnectEnd={onConnectEnd} isValidConnection={isValidConnection}
          defaultViewport={detail.graph.viewport || { x: 0, y: 0, zoom: 1 }} fitView={!detail.graph.viewport && nodes.length > 0} fitViewOptions={{ padding: INITIAL_FIT_PADDING, maxZoom: 1 }} onBeforeDelete={onBeforeDelete}
          onMoveEnd={markDirty} minZoom={0.1} maxZoom={2} colorMode={themeMode()} deleteKeyCode={['Backspace', 'Delete']} proOptions={{ hideAttribution: true }}>
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
          <Controls position="bottom-left" showFitView={false}>
            <ControlButton onClick={() => fitAll()} aria-label="适应画面" title="适应画面"><Maximize size={12} /></ControlButton>
          </Controls>
          {hasCharacterRefs && <Panel position="bottom-left" className="nc-legend-panel"><span className="nc-legend"><span className="nc-legend__line" aria-hidden="true" />虚线：角色参考</span></Panel>}
          <MiniMap position="bottom-right" pannable zoomable nodeStrokeWidth={2} style={{ width: 160, height: 104 }} />
          <Panel position="top-center" className="nc-top" ref={topRef}>
            <div className="nc-toolbar">
              <button className="nc-tool nc-tool--primary" disabled={nodes.length === 0 || running} onClick={() => void openBatch('all')}><Play size={14} />全部运行</button>
              <ToolbarMenu label="分段生成" icon={<Sparkles size={14} />} disabled={nodes.length === 0 || running}>
                {(close) => <>
                  <button role="menuitem" className="nc-menu__item" onClick={() => { close(); void openBatch('all', undefined, ['image']) }}><ImageIcon size={14} />生成全部画面</button>
                  <button role="menuitem" className="nc-menu__item" onClick={() => { close(); void openBatch('all', undefined, ['video']) }}><Film size={14} />生成全部视频</button>
                </>}
              </ToolbarMenu>
              <span className="nc-divider" />
              <button className="nc-tool" disabled={groups.length === 0} onClick={toggleAll}>
                {allCollapsed ? <ChevronsUpDown size={14} /> : <ChevronsDownUp size={14} />}{allCollapsed ? '全部展开' : '全部折叠'}
              </button>
              <button className="nc-tool" onClick={addShot}><Plus size={14} />添加镜头</button>
              <ToolbarMenu label="更多" icon={<MoreHorizontal size={14} />}>
                {(close) => <>
                  <button role="menuitem" className="nc-menu__item" onClick={() => { close(); addNode('text') }}><Type size={14} />新建文本节点</button>
                  <button role="menuitem" className="nc-menu__item" onClick={() => { close(); addNode('image') }}><ImageIcon size={14} />新建图片节点</button>
                  <button role="menuitem" className="nc-menu__item" onClick={() => { close(); addNode('video') }}><Film size={14} />新建视频节点</button>
                  <button role="menuitem" className="nc-menu__item" onClick={() => { close(); addNode('audio') }}><Mic size={14} />新建配音节点</button>
                  <button role="menuitem" className="nc-menu__item" disabled={nodes.length === 0} onClick={() => { close(); tidy() }}><LayoutGrid size={14} />整理布局</button>
                </>}
              </ToolbarMenu>
              <span className="nc-divider" />
              <span className="nc-meta">
                <span className={`nc-save nc-save--${saveState}`}>{saveState === 'saved' ? <Check size={11} /> : saveState === 'saving' ? <Loader2 size={11} className="nc-spin" /> : null}{saveLabel}</span>
                <span className="nc-spent" title="本画布累计花费">已花费 ¥{Number(runs?.spent || 0).toFixed(2)}</span>
              </span>
              {!copilot && <button className="nc-tool nc-tool--accent" aria-label="Copilot" title="Copilot" onClick={() => setCopilot(true)}><Sparkles size={14} /><span className="nc-tool__label">Copilot</span></button>}
            </div>
            {batch && <div className="nc-top__notes">
              {batch && <span className={`nc-batch-status nc-batch-status--${batch.status}`} role="status">
                {batch.status === 'running' && <Loader2 size={12} className="nc-spin" />}
                {kindsLabel(batch.kinds)} {batch.completed}/{batch.total} 完成{batch.failed > 0 ? ` · ${batch.failed} 失败` : ''}{batch.status === 'canceled' ? ' · 已取消' : ''}
                {batch.status === 'running' && <button className="nc-link" onClick={() => void cancelBatch()}>取消</button>}
              </span>}
              {batch && batch.id !== hintDismissed && batch.status !== 'running' && batch.status !== 'canceled' && batch.kinds?.length === 1 && batch.kinds[0] === 'image' && <span className="nc-batch-hint" role="status">
                <span className="nc-ellipsis">画面已完成：挑选满意的图，再点「生成全部视频」</span>
                <button className="nc-link" onClick={() => void openBatch('all', undefined, ['video'])}>生成全部视频</button>
                <button className="nc-icon" aria-label="关闭提示" onClick={() => setHintDismissed(batch.id)}><X size={12} /></button>
              </span>}
            </div>}
          </Panel>
          {nodes.length === 0 && <Panel position="top-center" className="nc-empty">
            从上方「更多」菜单添加文本 / 图片 / 视频节点，点「添加镜头」新建一组镜头，或打开 Copilot 把想法拆成镜头。拖动节点端口到空白处可直接建下游节点。
          </Panel>}
        </ReactFlow>
      </div>
      {plan && <BatchDialog plan={plan} onConfirm={() => void confirmBatch()} onClose={() => setPlan(null)} />}
      {copilot && <CopilotPanel canvasId={detail.id} onPlace={placeSplit} onClose={() => setCopilot(false)} />}
    </div>
  </CanvasContext.Provider>
}

export default function NodeCanvasPage({ canvasId }: { canvasId: string }) {
  const [detail, setDetail] = useState<CanvasDetail | null>(null)
  const [models, setModels] = useState<CanvasModels | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setDetail(null); setError('')
    nodeCanvasApi.get(canvasId).then((value) => { if (!cancelled) setDetail(value) })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : '画布加载失败') })
    nodeCanvasApi.models().then((value) => { if (!cancelled) setModels(value) }).catch(() => undefined)
    return () => { cancelled = true }
  }, [canvasId])

  if (error) return <div className="nc-message">{error} · <Link to={paths.projects}>返回项目</Link></div>
  if (!detail) return <div className="nc-message">画布加载中…</div>
  return <ReactFlowProvider><Canvas key={detail.id} detail={detail} models={models} /></ReactFlowProvider>
}
