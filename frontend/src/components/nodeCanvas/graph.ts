/**
 * 节点画布的纯逻辑：连线规则、节点输入解析、拖出端口建节点、Copilot 拆镜头的布局。
 * 不依赖 React，便于单测。
 */
import type { CanvasNodeType, CanvasRuns, NodeJob, NodeResult, RunRequest, SplitResult, StoredEdge, StoredNode, VideoTier } from '../../api/nodeCanvas'

export type InputHandle = 'prompt' | 'reference' | 'first_frame' | 'last_frame' | 'voice'

export interface TextData { label?: string; text?: string }
export interface ImageData {
  label?: string; prompt?: string; model?: string; count?: number; selected?: string; shot_size?: string; order?: number
  /** 角色图片节点：role === 'character'；同名 character 的多个节点是同一角色的多套造型 */
  role?: 'character'; character?: string; look?: string; library_asset_id?: string
}
export interface VideoData {
  label?: string; prompt?: string; model?: string; duration?: number; tier?: VideoTier; selected?: string; camera?: string; order?: number
  /** 切到“直接参考角色图”时被摘掉的首帧 / 尾帧图片节点 id：图片节点保留且仍归本镜头，切回时据此恢复连线 */
  parked_first_frame?: string; parked_last_frame?: string
}
/** 配音节点：text 台词，speaker 说话人（旁白写 NARRATOR），voice 预置音色 id（空 = 说话人默认音色），order 同一镜头里的先后 */
export interface AudioData { label?: string; text?: string; speaker?: string; voice?: string; selected?: string; order?: number }

export const NARRATOR = '旁白'
/** 台词上限（字符），与后端一致 */
export const MAX_LINE_CHARS = 500

/** 每种节点接受哪些输入端口，以及每个端口接受的上游类型。 */
export const INPUTS: Record<CanvasNodeType, Partial<Record<InputHandle, CanvasNodeType>>> = {
  text: {},
  image: { prompt: 'text', reference: 'image', voice: 'audio' },
  video: { prompt: 'text', reference: 'image', first_frame: 'image', last_frame: 'image', voice: 'audio' },
  audio: { prompt: 'text' },
  // 成片节点：自动收集全部镜头，没有输入输出
  episode: {},
}

export const HANDLE_LABELS: Record<InputHandle, string> = {
  prompt: '提示词',
  reference: '参考图',
  first_frame: '首帧',
  last_frame: '尾帧',
  voice: '台词',
}

/** 每个端口最多几条入线；提示词、参考图和台词（voice）可以多条，首尾帧各一条。 */
const SINGLE_INPUT = new Set<InputHandle>(['first_frame', 'last_frame'])
/** 图片节点的参考图上限 */
export const MAX_REFERENCES = 4
/** 视频节点的角色参考图上限（Seedance 2.0 mini 最多 9 张，与后端 MAX_REFERENCE_IMAGES 一致） */
export const MAX_VIDEO_REFERENCES = 9
/** 参考图与首帧 / 尾帧互斥（厂商会返回 400）；文案与后端一致 */
export const REFERENCE_MISSING = '还没有连角色参考图：连上角色，或改回「从首帧画面生成」'
export const REFERENCE_WITH_FRAME = '参考图和首帧不能同时使用：去掉首帧连线，或去掉参考图连线'

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
  if (handle === 'reference' && incoming >= (to.type === 'video' ? MAX_VIDEO_REFERENCES : MAX_REFERENCES)) return false
  return true
}

const parkedCache = new WeakMap<StoredNode[], Set<string>>()

/**
 * 被视频节点“停放”的首帧 / 尾帧图片 id：视频切到“直接参考角色图”时首帧连线被摘掉，图片节点保留且仍是镜头画面。
 * 角色判定（characters.ts / speakerVoice / groups）都要排除它们——它们剩下的出边可能全是给别的镜头当参考图的 reference。
 */
export function parkedFrameIds(nodes: StoredNode[]): Set<string> {
  const cached = parkedCache.get(nodes)
  if (cached) return cached
  const ids = new Set<string>()
  for (const node of nodes) {
    if (node.type !== 'video') continue
    const data = node.data as VideoData
    for (const id of [data.parked_first_frame, data.parked_last_frame]) if (typeof id === 'string' && id) ids.add(id)
  }
  parkedCache.set(nodes, ids)
  return ids
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

/**
 * 说话人角色的默认音色：角色基础造型节点的 data.voice，找不到为空。
 * 口径同 characters.ts（这里不能引用它，会形成循环依赖）：角色节点 = role 为 character 或出边全是 reference 的图片；基础造型 = look 为空 / “默认”。
 */
export function speakerVoice(nodes: StoredNode[], edges: StoredEdge[], speaker: string): string {
  const wanted = speaker.trim()
  if (!wanted) return ''
  const parked = parkedFrameIds(nodes)
  const characters = nodes.filter((node) => {
    if (node.type !== 'image' || parked.has(node.id)) return false
    if ((node.data as ImageData).role === 'character') return true
    const out = edges.filter((edge) => edge.source === node.id)
    return out.length > 0 && out.every((edge) => edge.targetHandle === 'reference')
  }).filter((node) => {
    const data = node.data as ImageData
    const name = String(data.character || '').trim() || String(data.label || '').trim().replace(/^\s*角色\s*[·.、:：-]\s*/, '').trim() || String(data.label || '').trim()
    return name === wanted
  })
  const base = characters.find((node) => { const look = String((node.data as ImageData).look || '').trim(); return look === '' || look === '默认' }) ?? characters[0]
  return base ? String((base.data as { voice?: string }).voice || '').trim() : ''
}

/** 配音节点的有效台词：prompt 边上的文本节点内容优先，否则取节点自己的 data.text。 */
function lineText(node: StoredNode, upstream: StoredNode[]): string {
  const linked = upstream.map((item) => String((item.data as TextData).text || '').trim()).filter(Boolean).join('\n')
  return linked || String((node.data as AudioData).text || '').trim()
}

/** 汇总一个图片 / 视频 / 配音节点的全部输入，得到提交给后端的请求。 */
export function resolveInputs(node: StoredNode, nodes: StoredNode[], edges: StoredEdge[], runs: CanvasRuns | null, aspectRatio: string): ResolvedInputs {
  const byId = new Map(nodes.map((item) => [item.id, item]))
  const incoming = (handle: InputHandle) => edges
    .filter((edge) => edge.target === node.id && edge.targetHandle === handle)
    .map((edge) => byId.get(edge.source))
    .filter((item): item is StoredNode => !!item)
  const blockers: string[] = []
  if (node.type === 'audio') {
    // 音色取值顺序：节点自己的 → 说话人角色的 → 空（系统默认音色）
    const data = node.data as AudioData
    const text = lineText(node, incoming('prompt'))
    if (!text) blockers.push('写台词，或连入一个文本节点')
    else if (text.length > MAX_LINE_CHARS) blockers.push(`台词不能超过 ${MAX_LINE_CHARS} 字`)
    const voice = String(data.voice || '').trim() || speakerVoice(nodes, edges, String(data.speaker || ''))
    return { upstreamPrompt: '', blockers, request: { kind: 'tts', text, voice } }
  }
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
  // 景别 / 运镜追加在提示词末尾（空则不加）；blockers 仍按追加前的提示词判断，与后端 resolve_node_request 一致
  const withSuffix = (suffixLabel: string, value: unknown) => {
    const text = String(value ?? '').trim()
    return [prompt, text ? `${suffixLabel}：${text}` : ''].filter(Boolean).join('\n')
  }

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
      request: { kind: 'image', prompt: withSuffix('景别', data.shot_size), model: data.model || '', aspect_ratio: aspectRatio, count: data.count || 1, reference_artifact_ids: references },
    }
  }
  const data = node.data as VideoData
  const hasFirst = incoming('first_frame').length > 0
  const first = frame('first_frame')
  const last = frame('last_frame')
  // 角色参考图：去重、保持连线顺序；还没有结果的上游和图片节点一样给出 blocker
  const references: string[] = []
  for (const item of incoming('reference')) {
    const result = selectedResult(item, runs)
    if (!result) blockers.push(`参考图节点「${label(item)}」还没有图片`)
    else if (!references.includes(result.artifact_id)) references.push(result.artifact_id)
  }
  if (incoming('reference').length && (hasFirst || incoming('last_frame').length)) blockers.push(REFERENCE_WITH_FRAME)
  // 切到参考图模式（首帧被停放）后参考边又被删光：不能悄悄按文生视频提交
  if ((data.parked_first_frame || data.parked_last_frame) && !incoming('reference').length && !hasFirst) blockers.push(REFERENCE_MISSING)
  if (incoming('last_frame').length && !hasFirst) blockers.push('有尾帧时也需要连入首帧')
  if (!prompt && !hasFirst) blockers.push('写提示词，或连入首帧图片')
  return {
    upstreamPrompt,
    blockers,
    request: {
      kind: 'video', prompt: withSuffix('运镜', data.camera), model: data.model || '', aspect_ratio: aspectRatio, duration: data.duration || 5, tier: data.tier || 'preview',
      first_frame_artifact_id: first, last_frame_artifact_id: last, reference_artifact_ids: references,
    },
  }
}

export function label(node: StoredNode): string {
  const text = String((node.data as TextData).label || '').trim()
  if (text) return text
  return { text: '文本', image: '图片', video: '视频', audio: '配音', episode: '成片' }[node.type]
}

export function newId(prefix: CanvasNodeType): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10)
  return `${prefix}-${random}`
}

/** 每次点击生成一个幂等标识；失败自动重试时复用同一个。 */
export function newRequestId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

export function edgeId(source: string, target: string, handle: string): string {
  return `e-${source}-${target}-${handle}`
}

export function defaultData(type: CanvasNodeType): Record<string, unknown> {
  if (type === 'text') return { label: '', text: '' }
  if (type === 'image') return { label: '', prompt: '', model: '', count: 1 }
  if (type === 'audio') return { label: '', text: '', speaker: '', voice: '' }
  if (type === 'episode') return { label: '成片 · 第 1 集' }
  return { label: '', prompt: '', model: '', duration: 5, tier: 'preview' }
}

/**
 * 从端口拖到空白处时自动建的下游 / 上游节点。
 * 从输出端口拖出：文本 → 图片（提示词）、图片 → 视频（首帧）、配音 → 视频（台词）。
 * 从输入端口拖出：建一个能接到该端口的上游节点。
 */
export function dropTarget(fromType: CanvasNodeType, handleType: 'source' | 'target', handle: string | null): { type: CanvasNodeType; handle: InputHandle; reverse: boolean } | null {
  if (handleType === 'source') {
    if (fromType === 'text') return { type: 'image', handle: 'prompt', reverse: false }
    if (fromType === 'image') return { type: 'video', handle: 'first_frame', reverse: false }
    if (fromType === 'audio') return { type: 'video', handle: 'voice', reverse: false }
    return null
  }
  const input = handle as InputHandle | null
  const upstream = input ? INPUTS[fromType][input] : undefined
  return upstream && input ? { type: upstream, handle: input, reverse: true } : null
}

const COLUMN = { character: 0, keyframe: 420, video: 840, audio: 1260 }
/** 同一镜头的多句台词纵向排开的间距（3 句仍在一个镜头行 ROW 之内） */
const LINE_STEP = 230
/** 行距要放得下带缩略图的图片 / 视频节点（约 520–580）加镜头组标题和边距，否则新拆出来的镜头组会互相重叠 */
const ROW = 700

/** 把 Copilot 拆出的角色与镜头摆成四列：角色定妆图 → 镜头关键帧 → 视频 → 台词（配音节点，连到该镜头视频的 voice 端口）。 */
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
      data: {
        label: `角色 · ${character.name}`, prompt: `${character.name}，${character.description}，角色定妆照，正面半身，纯色背景`, model: '', count: 4,
        role: 'character', character: character.name,
      },
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
    ;(shot.lines || []).forEach((line, order) => {
      const text = String(line.text || '').trim()
      if (!text) return
      const speaker = String(line.speaker || '').trim() || NARRATOR
      const audio = newId('audio')
      nodes.push({ id: audio, type: 'audio', position: { x: left + COLUMN.audio, y: y + order * LINE_STEP }, data: { label: `台词 · ${speaker}`, speaker, text, voice: '', order } })
      edges.push({ id: edgeId(audio, video, 'voice'), source: audio, target: video, sourceHandle: 'out', targetHandle: 'voice' })
    })
  })
  return { nodes, edges }
}

/** 节点最新一次任务的状态，用于在节点上显示进度。 */
export function latestJob(runs: CanvasRuns | null, nodeId: string) {
  return runs?.nodes[nodeId]?.jobs[0]
}

export const ACTIVE = new Set(['pending', 'leased', 'running', 'queued'])

export const STATUS: Record<string, string> = { pending: '排队中', queued: '排队中', leased: '准备中', running: '生成中', succeeded: '完成', failed: '失败', canceled: '已取消' }

/** 节点右上角的状态文字：生成中带百分比，排队时带前面的任务数。 */
export function statusText(job: NodeJob): string {
  const label = STATUS[job.status] || job.status
  if (job.status === 'running' && job.progress > 0) return `${label} ${job.progress}%`
  if ((job.status === 'queued' || job.status === 'pending') && job.queue_position > 0) return `${label} · 前面 ${job.queue_position} 个`
  return label
}
