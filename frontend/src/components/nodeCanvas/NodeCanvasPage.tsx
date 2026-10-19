import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background, BackgroundVariant, Controls, MiniMap, Panel, ReactFlow, ReactFlowProvider, addEdge,
  useEdgesState, useNodesState, useReactFlow, type Connection, type Edge, type Node, type OnConnectEnd,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ArrowLeft, Check, Film, ImageIcon, Loader2, Sparkles, Type, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  ConflictError, nodeCanvasApi, type CanvasDetail, type CanvasModels, type CanvasNodeType, type CanvasRuns, type RunRequest,
  type SplitResult, type StoredEdge, type StoredGraph, type StoredNode,
} from '../../api/nodeCanvas'
import { paths } from '../../routePaths'
import { toast } from '../ui/Toast'
import { CanvasContext, type CanvasActions } from './context'
import { canConnect, defaultData, dropTarget, edgeId, layoutSplit, newId } from './graph'
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
  const [title, setTitle] = useState(detail.title)
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const [copilot, setCopilot] = useState(false)
  const [polling, setPolling] = useState(true)
  const revision = useRef(detail.revision)
  const latest = useRef({ nodes, edges })
  const pending = useRef<Promise<void> | null>(null)
  const dirty = useRef(false)
  const loaded = useRef(false)
  const flow = useReactFlow()
  latest.current = { nodes, edges }

  const save = useCallback(async () => {
    if (pending.current) await pending.current
    if (!dirty.current) return
    dirty.current = false
    setSaveState('saving')
    const graph: StoredGraph = { nodes: stored(latest.current.nodes), edges: storedEdges(latest.current.edges), viewport: flow.getViewport() }
    pending.current = nodeCanvasApi.save(detail.id, graph, revision.current)
      .then((result) => { revision.current = result.revision; setSaveState(dirty.current ? 'dirty' : 'saved') })
      .catch(async (error) => {
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

  const updateData = useCallback((nodeId: string, patch: Record<string, unknown>) => {
    setNodes((items) => items.map((node) => node.id === nodeId ? { ...node, data: { ...node.data, ...patch } } : node))
  }, [setNodes])

  const flushThen = useCallback(async () => {
    if (dirty.current || pending.current) await save()
  }, [save])

  const actions: CanvasActions = useMemo(() => ({
    canvasId: detail.id,
    aspectRatio: detail.aspect_ratio,
    models,
    runs,
    nodes: stored(nodes),
    edges: storedEdges(edges),
    updateData,
    quote: (request: RunRequest) => nodeCanvasApi.quote(detail.id, request),
    run: async (nodeId: string, request: RunRequest) => {
      try {
        await flushThen()
        await nodeCanvasApi.run(detail.id, nodeId, request)
        await refreshRuns()
        setPolling(true)
      } catch (error) { toast(error instanceof Error ? error.message : '提交失败', 'error') }
    },
    upload: async (nodeId: string, file: File) => {
      try {
        await flushThen()
        const result = await nodeCanvasApi.upload(detail.id, nodeId, file)
        updateData(nodeId, { selected: result.artifact_id })
        await refreshRuns()
      } catch (error) { toast(error instanceof Error ? error.message : '上传失败', 'error') }
    },
  }), [detail.id, detail.aspect_ratio, models, runs, nodes, edges, updateData, flushThen, refreshRuns])

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
            <span className={`nc-save nc-save--${saveState}`}>{saveState === 'saved' ? <Check size={12} /> : saveState === 'saving' ? <Loader2 size={12} className="nc-spin" /> : null}{saveLabel}</span>
            <span className="nc-spent" title="本画布累计花费">已花费 ¥{Number(runs?.spent || 0).toFixed(2)}</span>
            {!copilot && <button className="nc-tool nc-tool--accent" onClick={() => setCopilot(true)}><Sparkles size={14} />Copilot</button>}
          </Panel>
          {nodes.length === 0 && <Panel position="top-center" className="nc-empty">
            从上方添加文本 / 图片 / 视频节点，或打开 Copilot 把想法拆成镜头。拖动节点端口到空白处可直接建下游节点。
          </Panel>}
        </ReactFlow>
      </div>
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
