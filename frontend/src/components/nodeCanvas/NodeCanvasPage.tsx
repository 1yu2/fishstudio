import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background, BackgroundVariant, Controls, MiniMap, Panel, ReactFlow, ReactFlowProvider, addEdge,
  useEdgesState, useNodesState, useReactFlow, type Connection, type Edge, type Node, type OnConnectEnd,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ArrowLeft, Check, Film, ImageIcon, Loader2, Play, Sparkles, Type, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  ConflictError, nodeCanvasApi, type BatchKind, type BatchQuote, type BatchRun, type BatchScope, type CanvasDetail, type DailyBudget, type CanvasModels, type CanvasNodeType, type CanvasRuns, type RunRequest,
  type SplitResult, type StoredEdge, type StoredGraph, type StoredNode,
} from '../../api/nodeCanvas'
import { paths } from '../../routePaths'
import { toast } from '../ui/Toast'
import { CanvasContext, type CanvasActions } from './context'
import { canConnect, defaultData, dropTarget, edgeId, layoutSplit, newId, newRequestId } from './graph'
import { nodeTypes } from './nodes'
import './nodeCanvas.css'

type SaveState = 'saved' | 'saving' | 'dirty' | 'error'
const SAVE_DELAY = 800
const POLL_INTERVAL = 3000

function toFlow(graph: StoredGraph): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: (graph.nodes || []).map((node) => ({ id: node.id, type: node.type, position: node.position, data: node.data })),
    edges: (graph.edges || []).map((edge) => ({ ...edge, sourceHandle: edge.sourceHandle ?? 'out' })),
  }
}

function stored(nodes: Node[]): StoredNode[] {
  return nodes.map((node) => ({ id: node.id, type: node.type as CanvasNodeType, position: node.position, data: node.data as Record<string, unknown> }))
}

function storedEdges(edges: Edge[]): StoredEdge[] {
  return edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target, sourceHandle: edge.sourceHandle ?? null, targetHandle: edge.targetHandle ?? null }))
}

/** 今日云端花费 / 上限；进行中的任务按预估计入，剩余不足两成时变色。 */
function BudgetBadge({ budget }: { budget: DailyBudget }) {
  const cap = budget.cap ?? 0
  const used = budget.spent + budget.in_flight
  const low = cap > 0 && (budget.remaining ?? 0) < cap * 0.2
  return <span className={`nc-budget${low ? ' nc-budget--low' : ''}`}
    title={`今日云端已花 ¥${budget.spent.toFixed(2)}，进行中预估 ¥${budget.in_flight.toFixed(2)}；本地预览不计入`}>
    今日云端 ¥{used.toFixed(2)} / ¥{cap.toFixed(0)}
  </span>
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

interface BatchPlan { scope: BatchScope; nodeId?: string; kinds?: BatchKind[]; requestId: string; quote: BatchQuote | null; error: string; notice: string; busy: boolean }

/** 分段批次的称呼：只含图片 = 画面批次，只含视频 = 视频批次，其余 = 批量 */
const kindsLabel = (kinds?: BatchKind[] | null) => {
  if (kinds?.length === 1) return kinds[0] === 'image' ? '画面批次' : '视频批次'
  return '批量'
}
const dialogTitle = (plan: BatchPlan) => {
  if (plan.kinds?.length === 1) return plan.kinds[0] === 'image' ? '生成全部画面' : '生成全部视频'
  return plan.scope === 'all' ? '全部运行' : '运行此节点及下游'
}

const money = (value: number) => `¥${value.toFixed(value < 1 ? 3 : 2)}`

/** 批量运行确认：先看合计报价和不能运行 / 价格未知的节点，确认后才提交。 */
function BatchDialog({ plan, onConfirm, onClose }: { plan: BatchPlan; onConfirm: () => void; onClose: () => void }) {
  const { quote } = plan
  const skipped = quote?.nodes.filter((item) => item.status === 'skipped').length ?? 0
  // 旧版服务端没有这些字段：按原来的口径（待运行节点都算）回退，不误判成“没有可运行的节点”
  const runnable = quote?.runnable_count ?? (quote ? quote.nodes.length - skipped : 0)
  const blocking = quote?.blocking_count ?? 0
  const notes = [
    skipped > 0 ? `${skipped} 个已有结果，跳过` : '',
    quote?.waiting_count ? `${quote.waiting_count} 个缺上游图片，先跳过` : '',
  ].filter(Boolean).join('；')
  const cannotRun = quote?.unavailable_nodes.filter((item) => item.runs === false) ?? []
  const unpriced = quote?.unavailable_nodes.filter((item) => item.runs !== false) ?? []
  return <aside className="nc-copilot nc-batch" aria-label="批量运行">
    <header><Play size={15} /><strong>{dialogTitle(plan)}</strong><button className="nc-icon" aria-label="关闭批量运行" onClick={onClose}><X size={14} /></button></header>
    {!quote && !plan.error && <p className="nc-copilot__hint"><Loader2 size={13} className="nc-spin" /> 正在报价…</p>}
    {plan.notice && <p className="nc-batch__warn" role="status">{plan.notice}</p>}
    {plan.error && <div className="nc-error nc-copilot__error" role="alert">{plan.error}</div>}
    {quote && <div className="nc-copilot__result">
      {blocking > 0
        ? <p className="nc-batch__warn">有 {blocking} 个节点需要先处理，处理后才能运行</p>
        : runnable === 0
          ? <p className="nc-batch__total">没有可以运行的节点{notes ? `（${notes}）` : ''}</p>
          : <p className="nc-batch__total">将运行 {runnable} 个节点{notes ? `（${notes}）` : ''}，预计合计 <b>{money(quote.estimated_total)}</b></p>}
      {quote.budget.remaining !== null && <p className={quote.exceeds_budget ? 'nc-batch__warn' : 'nc-copilot__hint'}>
        今日剩余额度 {money(quote.budget.remaining)}{quote.exceeds_budget ? `，云端预计 ${money(quote.billable_total)}，超出额度无法运行` : ''}
      </p>}
      {cannotRun.length > 0 && <section><h4>这些节点暂时无法运行（未计入合计）</h4>
        {cannotRun.map((item) => <div key={item.node_id} className="nc-copilot__item"><b>{item.label}</b>{item.reason}</div>)}
      </section>}
      {unpriced.length > 0 && <section><h4>价格未知（未计入合计）</h4>
        {unpriced.map((item) => <div key={item.node_id} className="nc-copilot__item"><b>{item.label}</b>{item.reason}</div>)}
      </section>}
      <button className="nc-run nc-run--wide" disabled={plan.busy || quote.exceeds_budget || blocking > 0 || runnable === 0} onClick={onConfirm}>
        {plan.busy ? <Loader2 size={13} className="nc-spin" /> : <Play size={13} />}确认运行
      </button>
    </div>}
  </aside>
}

function Canvas({ detail, models }: { detail: CanvasDetail; models: CanvasModels | null }) {
  const initial = useMemo(() => toFlow(detail.graph), [detail])
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initial.nodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initial.edges)
  const [runs, setRuns] = useState<CanvasRuns | null>(null)
  const [title, setTitle] = useState(detail.title)
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

  const save = useCallback(async () => {
    if (pending.current) await pending.current
    if (!dirty.current) return
    dirty.current = false
    saveFailed.current = false
    setSaveState('saving')
    const graph: StoredGraph = { nodes: stored(latest.current.nodes), edges: storedEdges(latest.current.edges), viewport: flow.getViewport() }
    pending.current = nodeCanvasApi.save(detail.id, graph, revision.current)
      .then((result) => { revision.current = result.revision; setSaveState(dirty.current ? 'dirty' : 'saved') })
      .catch(async (error) => {
        saveFailed.current = true
        setSaveState('error')
        if (error instanceof ConflictError) {
          toast('画布在别处被修改，已加载最新版本', 'error')
          const fresh = await nodeCanvasApi.get(detail.id)
          const next = toFlow(fresh.graph)
          revision.current = fresh.revision
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

  useEffect(() => {
    if (!loaded.current) { loaded.current = true; return }
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
  const loadQuote = useCallback(async (scope: BatchScope, nodeId?: string, notice = '', kinds?: BatchKind[]) => {
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

  const openBatch = useCallback((scope: BatchScope, nodeId?: string, kinds?: BatchKind[]) => loadQuote(scope, nodeId, '', kinds), [loadQuote])

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

  const actions: CanvasActions = useMemo(() => ({
    canvasId: detail.id,
    aspectRatio: detail.aspect_ratio,
    models,
    runs,
    nodes: stored(nodes),
    edges: storedEdges(edges),
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
  }), [detail.id, detail.aspect_ratio, models, runs, nodes, edges, updateData, flushThen, refreshRuns, openBatch])

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
    window.setTimeout(() => flow.fitView({ nodes: laid.nodes.map((node) => ({ id: node.id })), padding: 0.15, duration: 400 }), 50)
  }, [flow, setEdges, setNodes])

  const rename = async () => {
    const next = title.trim()
    if (!next || next === detail.title) return
    try { await nodeCanvasApi.rename(detail.id, { title: next }) } catch (error) { toast(error instanceof Error ? error.message : '重命名失败', 'error') }
  }

  const saveLabel = { saved: '已保存', saving: '保存中…', dirty: '未保存', error: '保存失败' }[saveState]

  return <CanvasContext.Provider value={actions}>
    <div className={`nc-page${copilot ? ' nc-page--copilot' : ''}`}>
      <div className="nc-flow">
        <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
          onConnect={onConnect} onConnectEnd={onConnectEnd} isValidConnection={isValidConnection}
          defaultViewport={detail.graph.viewport || { x: 0, y: 0, zoom: 1 }} fitView={!detail.graph.viewport && nodes.length > 0} fitViewOptions={{ padding: 0.2 }}
          onMoveEnd={markDirty} minZoom={0.1} maxZoom={2} colorMode={themeMode()} deleteKeyCode={['Backspace', 'Delete']} proOptions={{ hideAttribution: true }}>
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
          <Controls position="bottom-left" />
          <MiniMap position="bottom-right" pannable zoomable nodeStrokeWidth={2} style={{ width: 160, height: 104 }} />
          <Panel position="top-left" className="nc-toolbar">
            <Link className="nc-icon" to={paths.projects} aria-label="返回项目"><ArrowLeft size={15} /></Link>
            <input className="nc-title" aria-label="画布名称" value={title} onChange={(event) => setTitle(event.target.value)} onBlur={() => void rename()}
              onKeyDown={(event) => { if (event.key === 'Enter') (event.target as HTMLInputElement).blur() }} />
            <span className="nc-divider" />
            <button className="nc-tool" onClick={() => addNode('text')}><Type size={14} />文本</button>
            <button className="nc-tool" onClick={() => addNode('image')}><ImageIcon size={14} />图片</button>
            <button className="nc-tool" onClick={() => addNode('video')}><Film size={14} />视频</button>
            <span className="nc-divider" />
            {(['image', 'video'] as const).map((kind) => <button key={kind} className="nc-tool" disabled={nodes.length === 0 || batch?.status === 'running'} onClick={() => void openBatch('all', undefined, [kind])}>
              {kind === 'image' ? <ImageIcon size={14} /> : <Film size={14} />}{kind === 'image' ? '生成全部画面' : '生成全部视频'}
            </button>)}
            <button className="nc-tool" disabled={nodes.length === 0 || batch?.status === 'running'} onClick={() => void openBatch('all')}><Play size={14} />全部运行</button>
            {batch && <span className={`nc-batch-status nc-batch-status--${batch.status}`} role="status">
              {batch.status === 'running' && <Loader2 size={12} className="nc-spin" />}
              {kindsLabel(batch.kinds)} {batch.completed}/{batch.total} 完成{batch.failed > 0 ? ` · ${batch.failed} 失败` : ''}{batch.status === 'canceled' ? ' · 已取消' : ''}
              {batch.status === 'running' && <button className="nc-link" onClick={() => void cancelBatch()}>取消</button>}
            </span>}
            {batch && batch.id !== hintDismissed && batch.status !== 'running' && batch.status !== 'canceled' && batch.kinds?.length === 1 && batch.kinds[0] === 'image' && <span className="nc-batch-hint" role="status">
              画面已完成：挑选满意的图，再点「生成全部视频」
              <button className="nc-link" onClick={() => void openBatch('all', undefined, ['video'])}>生成全部视频</button>
              <button className="nc-icon" aria-label="关闭提示" onClick={() => setHintDismissed(batch.id)}><X size={12} /></button>
            </span>}
            <span className="nc-divider" />
            <span className={`nc-save nc-save--${saveState}`}>{saveState === 'saved' ? <Check size={12} /> : saveState === 'saving' ? <Loader2 size={12} className="nc-spin" /> : null}{saveLabel}</span>
            <span className="nc-spent" title="本画布累计花费">已花费 ¥{Number(runs?.spent || 0).toFixed(2)}</span>
            {runs?.budget?.cap != null && <BudgetBadge budget={runs.budget} />}
            {!copilot && <button className="nc-tool nc-tool--accent" onClick={() => setCopilot(true)}><Sparkles size={14} />Copilot</button>}
          </Panel>
          {nodes.length === 0 && <Panel position="top-center" className="nc-empty">
            从上方添加文本 / 图片 / 视频节点，或打开 Copilot 把想法拆成镜头。拖动节点端口到空白处可直接建下游节点。
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
