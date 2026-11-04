/**
 * 角色页的角色模型：从画布 graph 推导角色 / 造型 / 状态，以及新建、改名等纯函数。
 * 角色 = 画布里的角色图片节点（data.role === 'character'，或兼容旧画布：出边全部是 reference 的图片节点）；
 * 同名 character 的多个节点是同一角色的多套造型。不保存额外状态，不依赖 React，便于单测。
 */
import type { CanvasRuns, NodeJob, NodeResult, StoredEdge, StoredNode } from '../../api/nodeCanvas'
import { ACTIVE, newId, parkedFrameIds, type ImageData } from '../nodeCanvas/graph'

export const DEFAULT_LOOK = '默认'
export const CANDIDATE_COUNT = 4
const ROW = 380
const COLUMN = 420
const LABEL_PREFIX = /^\s*角色\s*[·.、:：-]\s*/

/** 角色节点：role 为 character，或旧画布里出边非空且全是 reference 的图片节点（被视频停放的首帧 / 尾帧图片除外）。 */
export function isCharacterNode(node: StoredNode, edges: StoredEdge[], nodes?: StoredNode[]): boolean {
  if (node.type !== 'image') return false
  // 被视频节点停放的首帧图片永远是镜头画面（传入 nodes 才能判断）
  if (nodes && parkedFrameIds(nodes).has(node.id)) return false
  if ((node.data as ImageData).role === 'character') return true
  const out = edges.filter((edge) => edge.source === node.id)
  return out.length > 0 && out.every((edge) => edge.targetHandle === 'reference')
}

/** 角色名：data.character，没有时从 label “角色 · 狐妖”里取。 */
export function characterName(node: StoredNode): string {
  const data = node.data as ImageData
  const name = String(data.character || '').trim()
  if (name) return name
  const label = String(data.label || '').trim()
  return label.replace(LABEL_PREFIX, '').trim() || label
}

/** 造型名；基础造型（空或“默认”）统一显示为“默认”。 */
export function lookName(node: StoredNode): string {
  const look = String((node.data as ImageData).look || '').trim()
  return look || DEFAULT_LOOK
}

export const isBaseLook = (node: StoredNode) => lookName(node) === DEFAULT_LOOK

export type CharacterState = 'final' | 'pick' | 'running' | 'empty' | 'failed'

export interface LookView {
  node: StoredNode
  name: string
  isBase: boolean
  state: CharacterState
  /** 定稿图（selected 且确实是这个节点的结果）；没有为 undefined */
  final?: NodeResult
  /** 全部结果，最新在前 */
  results: NodeResult[]
  job?: NodeJob
  progress: number
  queued: boolean
}

export interface Character {
  name: string
  looks: LookView[]
  /** 基础造型 */
  base: LookView
  description: string
  libraryAssetId?: string
  /** 基础造型已定稿 */
  finalized: boolean
}

function lookView(node: StoredNode, runs: CanvasRuns | null): LookView {
  const entry = runs?.nodes[node.id]
  const results = (entry?.results || []).filter((item) => item.type !== 'video')
  const selected = (node.data as ImageData).selected
  const final = selected ? results.find((item) => item.artifact_id === selected) : undefined
  const job = entry?.jobs[0]
  const active = job && ACTIVE.has(job.status) ? job : undefined
  const running = !!active
  const state: CharacterState = running ? 'running' : job?.status === 'failed' && !final ? 'failed' : final ? 'final' : results.length > 0 ? 'pick' : 'empty'
  return {
    node, name: lookName(node), isBase: isBaseLook(node), state, final, results, job: running || state === 'failed' ? job : undefined,
    progress: active?.status === 'running' ? active.progress : 0,
    queued: !!active && active.status !== 'running' && active.status !== 'leased',
  }
}

/** 画布 graph → 角色列表（按角色第一次出现的顺序；同一角色的造型保持画布顺序，基础造型排第一）。 */
export function buildCharacters(nodes: StoredNode[], edges: StoredEdge[], runs: CanvasRuns | null): Character[] {
  const groups = new Map<string, StoredNode[]>()
  for (const node of nodes) {
    if (!isCharacterNode(node, edges, nodes)) continue
    const name = characterName(node)
    groups.set(name, [...(groups.get(name) || []), node])
  }
  return [...groups.entries()].map(([name, members]) => {
    const baseNode = members.find(isBaseLook) ?? members[0]
    const ordered = [baseNode, ...members.filter((node) => node !== baseNode)]
    const looks = ordered.map((node) => lookView(node, runs))
    const base = looks[0]
    return {
      name, looks, base,
      description: String((baseNode.data as ImageData).prompt || ''),
      libraryAssetId: ((baseNode.data as ImageData).library_asset_id || undefined),
      finalized: !!base.final,
    }
  })
}

/** 底栏用：角色是否都已定稿（口径同后端：基础造型有 selected 且是该节点的结果）；没有角色算完成。 */
export function characterGate(graph: { nodes?: StoredNode[]; edges?: StoredEdge[] }, runs: CanvasRuns | null): { done: boolean; pending: string[] } {
  const pending = buildCharacters(graph.nodes || [], graph.edges || [], runs).filter((item) => !item.finalized).map((item) => item.name)
  return { done: pending.length === 0, pending }
}

/** 一个角色的候选图：最新一批（最多 count 张）在前，其余是更早的。 */
export function splitCandidates(results: NodeResult[], batch = CANDIDATE_COUNT): { latest: NodeResult[]; earlier: NodeResult[] } {
  return { latest: results.slice(0, batch), earlier: results.slice(batch) }
}

export function labelFor(name: string, look: string): string {
  return look && look !== DEFAULT_LOOK ? `角色 · ${name} · ${look}` : `角色 · ${name}`
}

export function describePrompt(name: string, description: string): string {
  const text = description.trim()
  return text ? `${name}，${text}，角色定妆照，正面半身，纯色背景` : `${name}，角色定妆照，正面半身，纯色背景`
}

/** 新角色节点：放在已有角色节点下方（没有角色时放在画布左侧）。 */
export function newCharacterNode(nodes: StoredNode[], edges: StoredEdge[], name: string, description: string): StoredNode {
  const existing = nodes.filter((node) => isCharacterNode(node, edges, nodes))
  const x = existing.length ? Math.min(...existing.map((node) => node.position.x)) : nodes.length ? Math.min(...nodes.map((node) => node.position.x)) - COLUMN : 0
  const y = existing.length ? Math.max(...existing.map((node) => node.position.y)) + ROW : 0
  return {
    id: newId('image'), type: 'image', position: { x, y },
    data: { label: labelFor(name, ''), role: 'character', character: name, look: '', prompt: describePrompt(name, description), model: '', count: CANDIDATE_COUNT },
  }
}

/** 新造型节点：同名角色节点，prompt = 基础描述 + “，造型：…”；放在已有角色节点下方。 */
export function newLookNode(nodes: StoredNode[], edges: StoredEdge[], character: Character, look: string, difference: string): StoredNode {
  const existing = nodes.filter((node) => isCharacterNode(node, edges, nodes))
  const base = character.base.node
  const text = difference.trim()
  return {
    id: newId('image'), type: 'image',
    position: { x: base.position.x, y: Math.max(...existing.map((node) => node.position.y), base.position.y) + ROW },
    data: {
      label: labelFor(character.name, look.trim()), role: 'character', character: character.name, look: look.trim(),
      prompt: `${character.description.trim()}${text ? `，造型：${text}` : ''}`, model: '', count: CANDIDATE_COUNT,
      ...(character.libraryAssetId ? { library_asset_id: character.libraryAssetId } : {}),
    },
  }
}

/** 编辑：改名同步到该角色所有造型节点的 character 与 label；描述写到基础造型的 prompt。 */
export function editCharacter(nodes: StoredNode[], character: Character, patch: { name: string; description: string }): StoredNode[] {
  const members = new Set(character.looks.map((look) => look.node.id))
  const name = patch.name.trim()
  return nodes.map((node) => {
    if (!members.has(node.id)) return node
    const data: Record<string, unknown> = { ...node.data, role: 'character', character: name, label: labelFor(name, String((node.data as ImageData).look || '').trim()) }
    if (node.id === character.base.node.id) data.prompt = patch.description
    return { ...node, data }
  })
}

/** 名字是否已被另一个角色占用。 */
export function nameTaken(nodes: StoredNode[], edges: StoredEdge[], name: string, except?: string): boolean {
  const wanted = name.trim()
  return nodes.some((node) => isCharacterNode(node, edges, nodes) && characterName(node) === wanted && wanted !== except)
}
