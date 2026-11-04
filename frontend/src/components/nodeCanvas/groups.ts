/**
 * 画布的镜头分组：把属于同一个镜头的节点（主节点 / 首帧图片 / 视频 / 只服务本镜头的提示词文本）框成一组，
 * 支持折叠成一张小卡片。分组完全由 graph 推导，不保存进 graph；折叠状态只存浏览器 localStorage。
 * 另含“把某块区域对到视口里并避开顶部工具栏”的纯函数。不依赖 React，便于单测。
 */
import type { CanvasRuns, NodeResult, StoredEdge, StoredNode } from '../../api/nodeCanvas'
import { isCharacterNode } from '../characters/characters'
import { buildShots, type MediaState, type MediaStatus } from '../storyboard/shots'

/** 派生的分组节点类型；它只存在于渲染用的节点列表里，绝不进入保存的 graph。 */
export const GROUP_NODE_TYPE = 'shotGroup'
export const groupNodeId = (shotId: string) => `shot-group:${shotId}`

export interface Rect { x: number; y: number; width: number; height: number }

/** 带测量尺寸的节点（React Flow 节点测量后有 measured）。 */
export type SizedNode = StoredNode & { measured?: { width?: number; height?: number } }

/** 没测量过时的默认尺寸：宽固定 280，高按节点类型估计。 */
export const DEFAULT_SIZE: Record<StoredNode['type'], { width: number; height: number }> = {
  text: { width: 280, height: 230 },
  image: { width: 280, height: 520 },
  video: { width: 280, height: 580 },
  audio: { width: 280, height: 330 },
  episode: { width: 220, height: 160 },
}
/** 分组框相对成员外框的内边距、顶部标题栏高度 */
export const GROUP_PADDING = 24
export const GROUP_HEADER = 44
/** 折叠后小卡片的尺寸 */
export const CARD_SIZE = { width: 232, height: 68 }

export interface ShotGroup {
  /** 分组节点 id */
  id: string
  /** 镜头主节点 id（折叠状态用它作键） */
  shotId: string
  /** 从 0 开始的镜头序号 */
  index: number
  title: string
  shotSize: string
  /** 有视频节点时的时长（秒） */
  duration: number | null
  caption: string
  /** 明确的进度状态文字（不含景别），如“画面生成中 40%”“视频已完成” */
  status: string
  state: MediaState
  thumbnail?: NodeResult
  /** 全部成员节点 id */
  members: string[]
  /** 展开时的外框（含标题栏） */
  bounds: Rect
}

/** 节点最后一次测到的尺寸（节点被隐藏后 React Flow 不再测量，要靠它沿用）。 */
export type SizeCache = Map<string, { width: number; height: number }>

/** 尺寸优先级：本次测量 → 缓存 → 默认值（nodeRect 里兜底）。把缓存补进没有 measured 的节点，其余函数照常用 measured。 */
export function withCachedSizes(nodes: SizedNode[], cache: SizeCache): SizedNode[] {
  return nodes.map((node) => {
    if (node.measured?.width && node.measured?.height) return node
    const cached = cache.get(node.id)
    return cached ? { ...node, measured: cached } : node
  })
}

export function nodeRect(node: SizedNode): Rect {
  const fallback = DEFAULT_SIZE[node.type] || DEFAULT_SIZE.image
  return {
    x: node.position.x, y: node.position.y,
    width: node.measured?.width || node.width || fallback.width,
    height: node.measured?.height || node.height || fallback.height,
  }
}

/** 一批矩形的外接矩形；空列表返回 null。 */
export function unionRect(rects: Rect[]): Rect | null {
  if (rects.length === 0) return null
  const left = Math.min(...rects.map((rect) => rect.x))
  const top = Math.min(...rects.map((rect) => rect.y))
  const right = Math.max(...rects.map((rect) => rect.x + rect.width))
  const bottom = Math.max(...rects.map((rect) => rect.y + rect.height))
  return { x: left, y: top, width: right - left, height: bottom - top }
}

/** 成员外框 → 分组框：四周加内边距，顶部再留标题栏。 */
export function groupBounds(members: Rect[]): Rect {
  const box = unionRect(members) || { x: 0, y: 0, width: 0, height: 0 }
  return {
    x: box.x - GROUP_PADDING, y: box.y - GROUP_PADDING - GROUP_HEADER,
    width: box.width + GROUP_PADDING * 2, height: box.height + GROUP_PADDING * 2 + GROUP_HEADER,
  }
}

/**
 * 画布 graph → 镜头分组（顺序与镜头一致）。
 * 成员 = 主节点 + 首帧图片 + 视频 + 只连到本组成员的上游文本节点 + 只通过 voice 边连到本组成员的配音节点；
 * 角色节点不属于任何组，被多个组共用的节点（共用首帧、一段提示词连了几个镜头、一句台词连了几个镜头）也不归任何组。
 */
export function buildGroups(nodes: SizedNode[], edges: StoredEdge[], runs: CanvasRuns | null): ShotGroup[] {
  const shots = buildShots(nodes, edges, runs)
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const claims = new Map<string, number>()
  const cores = shots.map((shot) => {
    const ids = [...new Set([shot.main.id, shot.frame?.id, shot.video?.id].filter((id): id is string => !!id))]
      .filter((id) => { const node = byId.get(id); return !!node && !isCharacterNode(node, edges, nodes) })
    for (const id of ids) claims.set(id, (claims.get(id) || 0) + 1)
    return ids
  })
  // 上游文本 / 台词：所有出边都指向同一个组的核心成员才归这个组
  const owner = new Map<string, number>()
  cores.forEach((ids, index) => { for (const id of ids) if (claims.get(id) === 1) owner.set(id, index) })
  const texts = new Map<number, string[]>()
  for (const node of nodes) {
    if (node.type !== 'text' && node.type !== 'audio') continue
    const out = edges.filter((edge) => edge.source === node.id)
    if (out.length === 0) continue
    // 配音节点只认 voice 边；它还连了别的东西（理论上不会）就不归组
    if (node.type === 'audio' && out.some((edge) => edge.targetHandle !== 'voice')) continue
    const groups = new Set(out.map((edge) => owner.get(edge.target)))
    if (groups.size !== 1) continue
    const [group] = [...groups]
    if (group === undefined) continue
    texts.set(group, [...(texts.get(group) || []), node.id])
  }
  return shots.map((shot, index) => {
    const members = pruneMembers(shot.id, [...cores[index].filter((id) => claims.get(id) === 1), ...(texts.get(index) || [])], nodes, edges)
    const rects = members.map((id) => nodeRect(byId.get(id) as SizedNode))
    return {
      id: groupNodeId(shot.id), shotId: shot.id, index,
      title: shot.title, shotSize: shot.shotSize,
      duration: shot.video ? shot.duration : null,
      caption: shot.caption, status: groupStatus(shot.frameStatus, shot.videoStatus, !!shot.video), state: shot.state, thumbnail: shot.thumbnail,
      members,
      // 主节点被别的组占用时成员可能为空：退化成主节点自己的位置，保证小卡片有处可放
      bounds: groupBounds(rects.length ? rects : [nodeRect(shot.main)]),
    }
  })
}

/**
 * 去掉“还连着组外节点”的成员：成员只要有出边连到组外的非角色节点（例如镜头一的首帧又当参考图连到镜头二），
 * 它就被别处用到，折叠这一组不该把它一起藏起来。主节点永远保留；去掉一个后可能牵连别的，所以循环到稳定。
 */
function pruneMembers(mainId: string, candidates: string[], nodes: SizedNode[], edges: StoredEdge[]): string[] {
  const byId = new Map(nodes.map((node) => [node.id, node]))
  let members = new Set(candidates)
  for (;;) {
    const drop = [...members].filter((id) => id !== mainId && edges.some((edge) => {
      if (edge.source !== id || members.has(edge.target)) return false
      const target = byId.get(edge.target)
      return !!target && !isCharacterNode(target, edges, nodes)
    }))
    if (drop.length === 0) return candidates.filter((id) => members.has(id))
    members = new Set([...members].filter((id) => !drop.includes(id)))
  }
}

/**
 * 镜头组的进度状态文字：只说进度，不重复标题里的景别。
 * 视频进行中优先于画面进行中；失败其次；然后看完成情况。
 */
export function groupStatus(frame: MediaStatus, video: MediaStatus, hasVideo: boolean): string {
  const running = (name: string, status: MediaStatus) => status.queued ? `${name}排队中` : status.progress > 0 ? `${name}生成中 ${status.progress}%` : `${name}生成中`
  if (video.state === 'running') return running('视频', video)
  if (frame.state === 'running') return running('画面', frame)
  if (video.state === 'failed') return '视频失败'
  if (frame.state === 'failed') return '画面失败'
  if (video.state === 'done') return '视频已完成'
  if (frame.state === 'done') return hasVideo ? '图片已选，待转视频' : '画面已完成'
  return '未生成'
}

/** 折叠默认值：镜头多（> 4）默认全部折叠，否则全部展开。 */
export const COLLAPSE_THRESHOLD = 4
export function defaultCollapsed(shotIds: string[]): Set<string> {
  return new Set(shotIds.length > COLLAPSE_THRESHOLD ? shotIds : [])
}

export const collapseKey = (canvasId: string) => `fishstudio.canvas.collapsed.${canvasId}`

/** 读折叠状态；没存过（或读不出来）返回 null，由调用方用默认值。 */
export function loadCollapsed(canvasId: string): Set<string> | null {
  try {
    const raw = window.localStorage.getItem(collapseKey(canvasId))
    if (raw === null) return null
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? new Set(parsed.filter((item): item is string => typeof item === 'string')) : null
  } catch { return null }
}

export function saveCollapsed(canvasId: string, collapsed: Set<string>): void {
  try { window.localStorage.setItem(collapseKey(canvasId), JSON.stringify([...collapsed])) } catch { /* 存不了就只在本次有效 */ }
}

/** 把一个区域对到视口中央，但整体避开顶部工具栏（工具栏占 toolbarHeight 高，区域放在它下面的可见部分里居中）。返回新的视口。 */
export function focusViewport(
  rect: Rect, size: { width: number; height: number }, toolbarHeight: number,
  options: { padding?: number; maxZoom?: number; minZoom?: number } = {},
): { x: number; y: number; zoom: number } {
  const { padding = 48, maxZoom = 1, minZoom = 0.1 } = options
  const top = Math.max(0, Math.min(toolbarHeight, size.height - 1))
  const availableHeight = Math.max(size.height - top, 1)
  const fit = Math.min(
    Math.max(size.width - padding * 2, 1) / Math.max(rect.width, 1),
    Math.max(availableHeight - padding * 2, 1) / Math.max(rect.height, 1),
  )
  const zoom = Math.max(minZoom, Math.min(maxZoom, fit))
  const centerX = rect.x + rect.width / 2
  const centerY = rect.y + rect.height / 2
  return { x: size.width / 2 - centerX * zoom, y: top + availableHeight / 2 - centerY * zoom, zoom }
}

/** 整理布局的间距 */
const TIDY = { columnGap: 60, groupGap: 56, cardGap: 16, sectionGap: 140, stackGap: 24 }
/** 展开某组时，把被盖住的内容推到它底边之下留的间距 */
export const ROOM_GAP = 48

/** 组当前在画布上占的位置：折叠是小卡片，展开是整个框 */
export function visibleRect(group: ShotGroup, folded: boolean): Rect {
  return folded ? { x: group.bounds.x, y: group.bounds.y, ...CARD_SIZE } : group.bounds
}

/**
 * 整理布局：角色放最左一列；镜头按顺序从上到下排，组内按 文本 → 图片 → 视频 从左到右；
 * 不归组的其他节点放最右一列。纵向按当前显示的高度排：折叠的组只占小卡片，展开的组占实际高度（measured）加标题和边距。
 * 返回 节点 id → 新位置。
 */
export function tidyLayout(nodes: SizedNode[], edges: StoredEdge[], runs: CanvasRuns | null, collapsed: Set<string> = new Set()): Record<string, { x: number; y: number }> {
  const out: Record<string, { x: number; y: number }> = {}
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const groups = buildGroups(nodes, edges, runs)
  const grouped = new Set(groups.flatMap((group) => group.members))
  const characters = nodes.filter((node) => isCharacterNode(node, edges, nodes) && !grouped.has(node.id))
  const others = nodes.filter((node) => !grouped.has(node.id) && !characters.includes(node))
  const stack = (list: SizedNode[], x: number, top: number) => {
    let y = top
    for (const node of list) { out[node.id] = { x, y }; y += nodeRect(node).height + TIDY.stackGap }
    return y - TIDY.stackGap
  }
  const widthOf = (list: SizedNode[]) => Math.max(0, ...list.map((node) => nodeRect(node).width))

  let left = 0
  if (characters.length) { stack(characters, 0, 0); left = widthOf(characters) + TIDY.sectionGap }
  let top = 0  // 当前组框（或小卡片）的顶
  let right = left
  for (const group of groups) {
    const members = group.members.map((id) => byId.get(id)).filter((node): node is SizedNode => !!node)
    const cols = (['text', 'image', 'video', 'audio'] as const).map((type) => members.filter((node) => node.type === type)).filter((col) => col.length > 0)
    const first = top + GROUP_PADDING + GROUP_HEADER
    let x = left + GROUP_PADDING
    let bottom = first
    for (const col of cols) {
      bottom = Math.max(bottom, stack(col, x, first))
      x += widthOf(col) + TIDY.columnGap
    }
    right = Math.max(right, x - TIDY.columnGap + GROUP_PADDING)
    // 折叠的组成员被藏起来，只占小卡片那么高；卡片在组框左上角
    top = collapsed.has(group.shotId) ? top + CARD_SIZE.height + TIDY.cardGap : bottom + GROUP_PADDING + TIDY.groupGap
  }
  if (others.length) stack(others, groups.length ? right + TIDY.sectionGap : left, 0)
  return out
}

/**
 * 展开若干组并“让位”：依次（按给定顺序）展开，每展开一组，就把顶边在它之下、横向与它重叠的其他内容
 * （其他组整组、不归组的节点；不含角色）形成碰撞链往下推：只推真正碰到的，一个推到离展开底边 ROOM_GAP，再往下的依次留 ROOM_GAP，够远的不动。折叠时不回收空间。
 * 返回被移动节点的新位置，以及展开后的折叠集合。
 */
export function expandWithRoom(
  nodes: SizedNode[], edges: StoredEdge[], runs: CanvasRuns | null, collapsed: Set<string>, ids: string[],
): { positions: Record<string, { x: number; y: number }>; collapsed: Set<string> } {
  const work = new Map(nodes.map((node) => [node.id, { ...node }]))
  const current = new Set(collapsed)
  const moved = new Set<string>()
  for (const id of ids) {
    current.delete(id)
    const list = [...work.values()]
    const groups = buildGroups(list, edges, runs)
    const target = groups.find((group) => group.shotId === id)
    if (!target) continue
    const box = target.bounds
    const grouped = new Set(groups.flatMap((group) => group.members))
    const items: { rect: Rect; ids: string[] }[] = [
      ...groups.filter((group) => group !== target).map((group) => ({ rect: visibleRect(group, current.has(group.shotId)), ids: group.members })),
      ...list.filter((node) => !grouped.has(node.id) && !isCharacterNode(node, edges, nodes)).map((node) => ({ rect: nodeRect(node), ids: [node.id] })),
    ]
    const below = items.filter((item) => item.rect.y > box.y && item.rect.x + item.rect.width > box.x && item.rect.x < box.x + box.width)
    if (below.length === 0) continue
    // 碰撞链：按顶边从上到下，只有顶边低于“最低允许顶边”的才推到那里，推完它的底边再加间距成为下一个的下限；够远的不动
    below.sort((p, q) => p.rect.y - q.rect.y)
    let required = box.y + box.height + ROOM_GAP
    for (const item of below) {
      if (item.rect.y >= required) continue
      const delta = required - item.rect.y
      for (const memberId of item.ids) {
        const node = work.get(memberId)
        if (node) { node.position = { x: node.position.x, y: node.position.y + delta }; moved.add(memberId) }
      }
      required = Math.max(required, required + item.rect.height + ROOM_GAP)
    }
  }
  return { positions: Object.fromEntries([...moved].map((id) => [id, (work.get(id) as SizedNode).position])), collapsed: current }
}

/** 当前看得见的内容的最低边（隐藏的成员不算，折叠的组按小卡片算）；没有内容返回 null。 */
export function contentBottom(nodes: SizedNode[], groups: ShotGroup[], collapsed: Set<string> = new Set()): number | null {
  const hidden = new Set(groups.filter((group) => collapsed.has(group.shotId)).flatMap((group) => group.members))
  const rects = [
    ...nodes.filter((node) => !hidden.has(node.id)).map(nodeRect),
    ...groups.map((group) => visibleRect(group, collapsed.has(group.shotId))),
  ]
  return rects.length ? Math.max(...rects.map((rect) => rect.y + rect.height)) : null
}
