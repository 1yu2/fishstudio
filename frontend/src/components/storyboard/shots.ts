/**
 * 分镜页的镜头模型：从画布 graph 推导出镜头列表，以及排序 / 新增 / 修改链等纯函数。
 * 分镜页和画布是同一份数据的两种视图，这里不保存任何额外状态。不依赖 React，便于单测。
 */
import type { CanvasRuns, NodeResult, Quote, RunRequest, StoredEdge, StoredNode } from '../../api/nodeCanvas'
import { ACTIVE, edgeId, newId, selectedResult, type ImageData, type VideoData } from '../nodeCanvas/graph'

/** 景别与运镜的预设；运镜允许自定义文本。 */
export const SHOT_SIZES = ['远景', '全景', '中景', '近景', '特写'] as const
export const CAMERA_MOVES = ['固定', '缓慢推近', '拉远', '横移', '跟随', '环绕'] as const
export const DEFAULT_DURATION = 5
export const DURATIONS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

export type MediaState = 'none' | 'running' | 'failed' | 'done'

export interface MediaStatus {
  state: MediaState
  /** 生成中的进度（0–100），未知为 0 */
  progress: number
  /** 排队中（还没开始跑） */
  queued: boolean
}

export interface Shot {
  /** 主节点 id：有视频节点时是视频节点，否则是图片节点 */
  id: string
  /** 从 0 开始的位置 */
  index: number
  title: string
  main: StoredNode
  frame?: StoredNode
  video?: StoredNode
  description: string
  shotSize: string
  camera: string
  duration: number
  frameStatus: MediaStatus
  videoStatus: MediaStatus
  /** 卡片上的短状态文字，如“远景”“视频 60%”“失败”“未生成” */
  caption: string
  /** 卡片综合状态 */
  state: MediaState
  /** 缩略图：画面节点所选（否则最新）的图片结果 */
  thumbnail?: NodeResult
}

const NUMBER_PREFIX = /^\s*\d+\s*[·.、:：-]\s*/

/** 标题：去掉“3 · ”这类序号前缀；图片主节点还去掉“关键帧”后缀。 */
export function shotTitle(node: StoredNode, fallback: string): string {
  let text = String((node.data as { label?: string }).label || '').trim().replace(NUMBER_PREFIX, '')
  if (node.type === 'image') text = text.replace(/\s*关键帧$/, '')
  return text.trim() || fallback
}

function orderOf(node: StoredNode): number | null {
  const value = (node.data as { order?: unknown }).order
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function mediaStatus(node: StoredNode | undefined, runs: CanvasRuns | null): MediaStatus {
  if (!node) return { state: 'none', progress: 0, queued: false }
  const job = runs?.nodes[node.id]?.jobs[0]
  if (job && ACTIVE.has(job.status)) {
    return { state: 'running', progress: job.status === 'running' ? job.progress : 0, queued: job.status !== 'running' && job.status !== 'leased' }
  }
  if (job?.status === 'failed') return { state: 'failed', progress: 0, queued: false }
  return { state: selectedResult(node, runs) ? 'done' : 'none', progress: 0, queued: false }
}

function caption(frame: MediaStatus, video: MediaStatus, shotSize: string, hasFrame: boolean): { state: MediaState; caption: string } {
  const running = video.state === 'running' ? ['视频', video] as const : frame.state === 'running' ? ['画面', frame] as const : null
  if (running) {
    const [name, status] = running
    return { state: 'running', caption: status.queued ? `${name}排队中` : status.progress > 0 ? `${name} ${status.progress}%` : `${name}生成中` }
  }
  if (frame.state === 'failed' || video.state === 'failed') return { state: 'failed', caption: '失败' }
  // 没有画面节点（只有视频）时，完成与否看视频
  if ((hasFrame ? frame : video).state === 'none') return { state: 'none', caption: '未生成' }
  return { state: 'done', caption: shotSize || '完成' }
}

/**
 * 画布 graph → 镜头列表。
 * 镜头 = 每个视频节点（它的 first_frame 上游图片节点是画面节点）；另外，没有连到任何视频、且出边不全是 reference 的
 * 图片节点单独算一个“只有画面”的镜头；只通过 reference 边连出去的图片节点是角色图，不算镜头。
 * 顺序：主节点 data.order 升序，没有 order 的按画布位置 y、再 x 排在后面。
 * 口径说明：后端 workspace_overview_service 的 shot 是“视频节点，否则图片节点”，两者都有时与这里一致；
 * 差异只在“只有画面”的镜头（这里算镜头，后端只在没有任何视频节点时才把图片节点算作镜头）。
 */
export function buildShots(nodes: StoredNode[], edges: StoredEdge[], runs: CanvasRuns | null): Shot[] {
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const toVideo = new Set<string>()
  const outgoing = new Map<string, StoredEdge[]>()
  for (const edge of edges) {
    outgoing.set(edge.source, [...(outgoing.get(edge.source) || []), edge])
    if (byId.get(edge.target)?.type === 'video') toVideo.add(edge.source)
  }
  const mains: { main: StoredNode; frame?: StoredNode; video?: StoredNode }[] = []
  for (const node of nodes) {
    if (node.type === 'video') {
      const first = edges.find((edge) => edge.target === node.id && edge.targetHandle === 'first_frame')
      const frame = first ? byId.get(first.source) : undefined
      mains.push({ main: node, video: node, frame: frame?.type === 'image' ? frame : undefined })
    } else if (node.type === 'image' && !toVideo.has(node.id)) {
      const out = outgoing.get(node.id) || []
      if (out.length > 0 && out.every((edge) => edge.targetHandle === 'reference')) continue  // 角色图
      mains.push({ main: node, frame: node })
    }
  }
  mains.sort((a, b) => {
    const oa = orderOf(a.main)
    const ob = orderOf(b.main)
    if (oa !== null && ob !== null && oa !== ob) return oa - ob
    if (oa !== null && ob === null) return -1
    if (oa === null && ob !== null) return 1
    return a.main.position.y - b.main.position.y || a.main.position.x - b.main.position.x
  })
  return mains.map(({ main, frame, video }, index) => {
    const frameData = (frame?.data || {}) as ImageData
    const videoData = (video?.data || {}) as VideoData
    const frameStatus = mediaStatus(frame, runs)
    const videoStatus = mediaStatus(video, runs)
    const shotSize = String(frameData.shot_size || '').trim()
    const summary = caption(frameStatus, videoStatus, shotSize, !!frame)
    return {
      id: main.id,
      index,
      title: shotTitle(main, `镜头 ${index + 1}`),
      main, frame, video,
      description: String((frame ? frameData.prompt : videoData.prompt) || ''),
      shotSize,
      camera: String(videoData.camera || '').trim(),
      duration: videoData.duration || DEFAULT_DURATION,
      frameStatus, videoStatus,
      ...summary,
      thumbnail: frame ? selectedResult(frame, runs) : undefined,
    }
  })
}

/** 全部镜头的预计总时长（秒）：只算有视频节点的镜头。 */
export function totalSeconds(shots: Shot[]): number {
  return shots.reduce((sum, shot) => sum + (shot.video ? shot.duration : 0), 0)
}

/** 把第 from 个镜头移到第 to 个位置，返回 主节点 id → 新 order（给所有镜头重写 0..n-1）。 */
export function reorderPatch(shots: Shot[], from: number, to: number): Record<string, number> {
  if (from < 0 || from >= shots.length || to < 0 || to >= shots.length) return {}
  const ids = shots.map((shot) => shot.id)
  const [moved] = ids.splice(from, 1)
  ids.splice(to, 0, moved)
  return Object.fromEntries(ids.map((id, index) => [id, index]))
}

const ROW = 380
const FRAME_X = 0
const VIDEO_X = 420

/**
 * 末尾新增一个镜头：图片节点 + 视频节点，first_frame 相连，放在已有节点下方。
 * orders 是给已有镜头补写的 order（原来没有 order 的镜头不补会排到新镜头后面）。
 */
export function newShot(nodes: StoredNode[], shots: Shot[]): { nodes: StoredNode[]; edges: StoredEdge[]; orders: Record<string, number>; mainId: string } {
  const number = shots.length + 1
  const last = shots[shots.length - 1]
  const y = nodes.length ? Math.max(...nodes.map((node) => node.position.y)) + ROW : 0
  const frameX = last?.frame?.position.x ?? FRAME_X
  const videoX = last?.video?.position.x ?? frameX + VIDEO_X
  const frameId = newId('image')
  const videoId = newId('video')
  const title = `镜头 ${number}`
  return {
    nodes: [
      { id: frameId, type: 'image', position: { x: frameX, y }, data: { label: `${number} · ${title} 关键帧`, prompt: '', model: '', count: 1 } },
      { id: videoId, type: 'video', position: { x: videoX, y }, data: { label: `${number} · ${title}`, prompt: '', model: '', duration: DEFAULT_DURATION, tier: 'preview', order: shots.length } },
    ],
    edges: [{ id: edgeId(frameId, videoId, 'first_frame'), source: frameId, target: videoId, sourceHandle: 'out', targetHandle: 'first_frame' }],
    orders: Object.fromEntries(shots.map((shot, index) => [shot.id, index])),
    mainId: videoId,
  }
}

export interface ChainItem {
  result: NodeResult
  /** 第 n 张（原始 / 上传）或 改 n（基于某张修改而来） */
  label: string
  edited: boolean
  /** 这一版基于哪一版（label），原始结果没有 */
  basedOn?: string
}

/**
 * 修改链：把画面节点的结果按 edit_of 串起来。
 * 原始结果（没有 edit_of）按时间依次叫“第 1 张 / 第 2 张”，修改结果依次叫“改 1 / 改 2”。
 * runs 里的结果是最新在前，这里翻转成时间顺序。
 */
export function buildEditChain(results: NodeResult[]): ChainItem[] {
  const ordered = [...results].reverse()
  const labels = new Map<string, string>()
  let originals = 0
  let edits = 0
  for (const result of ordered) labels.set(result.artifact_id, result.edit_of ? `改 ${++edits}` : `第 ${++originals} 张`)
  const originalItems: ChainItem[] = []
  const editedItems: ChainItem[] = []
  for (const result of ordered) {
    const label = labels.get(result.artifact_id) || ''
    if (result.edit_of) editedItems.push({ result, label, edited: true, basedOn: labels.get(result.edit_of) })
    else originalItems.push({ result, label, edited: false })
  }
  return [...originalItems, ...editedItems]
}

export const EDIT_QUICK_PHRASES = ['换表情', '换动作', '换光线', '改背景'] as const
export const KEEP_COMPOSITION = '，保持构图和角色长相不变'

/** 追加快捷短语：已有内容时用逗号隔开。 */
export function appendPhrase(text: string, phrase: string): string {
  const trimmed = text.trimEnd()
  return trimmed ? `${trimmed}，${phrase}` : phrase
}

/** “按要求修改”的请求体：prompt 是修改要求（可附带保持构图），edit_instruction 记用户原文。 */
export function editRequest(args: { base: string; text: string; keep: boolean; count: number; model: string; aspectRatio: string }): RunRequest {
  const text = args.text.trim()
  return {
    kind: 'image', prompt: `${text}${args.keep ? KEEP_COMPOSITION : ''}`, model: args.model, aspect_ratio: args.aspectRatio,
    count: args.count, edit_of: args.base, edit_instruction: text.slice(0, 500),
  }
}

/** 带价格的按钮后缀：有价格 “ · ¥x”，没配置价格 “ · 未配置价格”，没有报价时为空。 */
export function priceSuffix(quote: Quote | null): string {
  if (!quote) return ''
  return ` · ${quote.estimated_cost != null ? moneyText(quote.estimated_cost) : '未配置价格'}`
}

function moneyText(value: number): string {
  return `¥${value.toFixed(value < 1 ? 3 : 2)}`
}
