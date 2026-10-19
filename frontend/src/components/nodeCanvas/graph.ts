/**
 * 节点画布的纯逻辑：连线规则、节点输入解析、拖出端口建节点、Copilot 拆镜头的布局。
 * 不依赖 React，便于单测。
 */
import type { CanvasNodeType, CanvasRuns, NodeResult, RunRequest, SplitResult, StoredEdge, StoredNode, VideoTier } from '../../api/nodeCanvas'

export type InputHandle = 'prompt' | 'reference' | 'first_frame' | 'last_frame'

export interface TextData { label?: string; text?: string }
export interface ImageData { label?: string; prompt?: string; model?: string; count?: number; selected?: string }
export interface VideoData { label?: string; prompt?: string; model?: string; duration?: number; tier?: VideoTier; selected?: string }

/** 每种节点接受哪些输入端口，以及每个端口接受的上游类型。 */
export const INPUTS: Record<CanvasNodeType, Partial<Record<InputHandle, CanvasNodeType>>> = {
  text: {},
  image: { prompt: 'text', reference: 'image' },
  video: { prompt: 'text', first_frame: 'image', last_frame: 'image' },
}

export const HANDLE_LABELS: Record<InputHandle, string> = {
  prompt: '提示词',
  reference: '参考图',
  first_frame: '首帧',
  last_frame: '尾帧',
}

/** 每个端口最多几条入线；提示词和参考图可以多条，首尾帧各一条。 */
const SINGLE_INPUT = new Set<InputHandle>(['first_frame', 'last_frame'])
const MAX_REFERENCES = 4

export function canConnect(
  nodes: Pick<StoredNode, 'id' | 'type'>[],
  edges: StoredEdge[],
  connection: { source: string | null; target: string | null; targetHandle?: string | null },
): boolean {
  const { source, target } = connection
  if (!source || !target || source === target) return false
  const from = nodes.find((node) => node.id === source)
  const to = nodes.find((node) => node.id === target)
  if (!from || !to) return false
  const handle = connection.targetHandle as InputHandle | undefined
  if (!handle || INPUTS[to.type][handle] !== from.type) return false
  if (edges.some((edge) => edge.source === source && edge.target === target && edge.targetHandle === handle)) return false
  const incoming = edges.filter((edge) => edge.target === target && edge.targetHandle === handle).length
  if (SINGLE_INPUT.has(handle) && incoming >= 1) return false
  if (handle === 'reference' && incoming >= MAX_REFERENCES) return false
  return true
}

/** 节点当前选中的结果：用户点选的那张，否则是最新一张。 */
export function selectedResult(node: StoredNode | undefined, runs: CanvasRuns | null): NodeResult | undefined {
  if (!node) return undefined
  const results = runs?.nodes[node.id]?.results || []
  const chosen = (node.data as ImageData).selected
  return results.find((result) => result.artifact_id === chosen) || results[0]
}

export interface ResolvedInputs {
  request: RunRequest
  /** 不能生成的原因；为空表示可以生成 */
  blockers: string[]
  /** 上游提供的提示词（不含节点自己的） */
  upstreamPrompt: string
}

/** 汇总一个图片 / 视频节点的全部输入，得到提交给后端的请求。 */
export function resolveInputs(node: StoredNode, nodes: StoredNode[], edges: StoredEdge[], runs: CanvasRuns | null, aspectRatio: string): ResolvedInputs {
  const byId = new Map(nodes.map((item) => [item.id, item]))
  const incoming = (handle: InputHandle) => edges
    .filter((edge) => edge.target === node.id && edge.targetHandle === handle)
    .map((edge) => byId.get(edge.source))
    .filter((item): item is StoredNode => !!item)
  const blockers: string[] = []
  const frame = (handle: InputHandle) => {
    const upstream = incoming(handle)[0]
    if (!upstream) return null
    const result = selectedResult(upstream, runs)
    if (!result) blockers.push(`${HANDLE_LABELS[handle]}节点「${label(upstream)}」还没有图片`)
    return result?.artifact_id || null
  }
  const upstreamPrompt = incoming('prompt').map((item) => String((item.data as TextData).text || '').trim()).filter(Boolean).join('\n')
  const own = String((node.data as ImageData).prompt || '').trim()
  const prompt = [upstreamPrompt, own].filter(Boolean).join('\n')

  if (node.type === 'image') {
    const data = node.data as ImageData
    const references = incoming('reference').map((item) => {
      const result = selectedResult(item, runs)
      if (!result) blockers.push(`参考图节点「${label(item)}」还没有图片`)
      return result?.artifact_id
    }).filter((id): id is string => !!id)
    if (!prompt) blockers.push('写提示词，或连入一个文本节点')
    return {
      upstreamPrompt,
      blockers,
      request: { kind: 'image', prompt, model: data.model || '', aspect_ratio: aspectRatio, count: data.count || 1, reference_artifact_ids: references },
    }
  }
  const data = node.data as VideoData
  const hasFirst = incoming('first_frame').length > 0
  const first = frame('first_frame')
  const last = frame('last_frame')
  if (incoming('last_frame').length && !hasFirst) blockers.push('有尾帧时也需要连入首帧')
  if (!prompt && !hasFirst) blockers.push('写提示词，或连入首帧图片')
  return {
    upstreamPrompt,
    blockers,
    request: {
      kind: 'video', prompt, model: data.model || '', aspect_ratio: aspectRatio, duration: data.duration || 5, tier: data.tier || 'preview',
      first_frame_artifact_id: first, last_frame_artifact_id: last,
    },
  }
}

export function label(node: StoredNode): string {
  const text = String((node.data as TextData).label || '').trim()
  if (text) return text
  return node.type === 'text' ? '文本' : node.type === 'image' ? '图片' : '视频'
}

export function newId(prefix: CanvasNodeType): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10)
  return `${prefix}-${random}`
}

export function edgeId(source: string, target: string, handle: string): string {
  return `e-${source}-${target}-${handle}`
}

export function defaultData(type: CanvasNodeType): Record<string, unknown> {
  if (type === 'text') return { label: '', text: '' }
  if (type === 'image') return { label: '', prompt: '', model: '', count: 1 }
  return { label: '', prompt: '', model: '', duration: 5, tier: 'preview' }
}

/**
 * 从端口拖到空白处时自动建的下游 / 上游节点。
 * 从输出端口拖出：文本 → 图片（提示词）、图片 → 视频（首帧）。
 * 从输入端口拖出：建一个能接到该端口的上游节点。
 */
export function dropTarget(fromType: CanvasNodeType, handleType: 'source' | 'target', handle: string | null): { type: CanvasNodeType; handle: InputHandle; reverse: boolean } | null {
  if (handleType === 'source') {
    if (fromType === 'text') return { type: 'image', handle: 'prompt', reverse: false }
    if (fromType === 'image') return { type: 'video', handle: 'first_frame', reverse: false }
    return null
  }
  const input = handle as InputHandle | null
  const upstream = input ? INPUTS[fromType][input] : undefined
  return upstream && input ? { type: upstream, handle: input, reverse: true } : null
}

const COLUMN = { character: 0, keyframe: 420, video: 840 }
const ROW = 380

/** 把 Copilot 拆出的角色与镜头摆成三列：角色定妆图 → 镜头关键帧 → 视频。 */
export function layoutSplit(split: SplitResult, existing: StoredNode[]): { nodes: StoredNode[]; edges: StoredEdge[] } {
  const top = existing.length ? Math.max(...existing.map((node) => node.position.y)) + ROW + 120 : 0
  const left = existing.length ? Math.min(...existing.map((node) => node.position.x)) : 0
  const nodes: StoredNode[] = []
  const edges: StoredEdge[] = []
  const characters = new Map<string, string>()
  split.characters.forEach((character, index) => {
    const id = newId('image')
    characters.set(character.name, id)
    nodes.push({
      id, type: 'image', position: { x: left + COLUMN.character, y: top + index * ROW },
      data: { label: `角色 · ${character.name}`, prompt: `${character.name}，${character.description}，角色定妆照，正面半身，纯色背景`, model: '', count: 1 },
    })
  })
  split.shots.forEach((shot, index) => {
    const y = top + index * ROW
    const keyframe = newId('image')
    const video = newId('video')
    const title = shot.title || `镜头 ${index + 1}`
    nodes.push({ id: keyframe, type: 'image', position: { x: left + COLUMN.keyframe, y }, data: { label: `${index + 1} · ${title} 关键帧`, prompt: shot.prompt, model: '', count: 1 } })
    nodes.push({ id: video, type: 'video', position: { x: left + COLUMN.video, y }, data: { label: `${index + 1} · ${title}`, prompt: shot.prompt, model: '', duration: shot.duration_s, tier: 'preview' } })
    shot.characters.map((name) => characters.get(name)).filter((id): id is string => !!id).slice(0, MAX_REFERENCES).forEach((source) => {
      edges.push({ id: edgeId(source, keyframe, 'reference'), source, target: keyframe, sourceHandle: 'out', targetHandle: 'reference' })
    })
    edges.push({ id: edgeId(keyframe, video, 'first_frame'), source: keyframe, target: video, sourceHandle: 'out', targetHandle: 'first_frame' })
  })
  return { nodes, edges }
}

/** 节点最新一次任务的状态，用于在节点上显示进度。 */
export function latestJob(runs: CanvasRuns | null, nodeId: string) {
  return runs?.nodes[nodeId]?.jobs[0]
}

export const ACTIVE = new Set(['pending', 'leased', 'running', 'queued'])
