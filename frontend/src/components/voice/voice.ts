/**
 * 配音的纯逻辑：从画布 graph 推导台词列表（按镜头）、说话人、每句的状态、超出镜头时长的判断，以及编辑台词时对 graph 的改写。
 * 台词 = 配音节点（type audio），通过 voice 边连到所属镜头的视频节点（只有画面的镜头连到图片节点）。
 * 不保存额外状态，不依赖 React，便于单测。
 */
import type { CanvasRuns, NodeResult, StoredEdge, StoredNode } from '../../api/nodeCanvas'
import { ACTIVE, NARRATOR, edgeId, newId, selectedResult, speakerVoice, type AudioData, type TextData, type VideoData } from '../nodeCanvas/graph'
import { characterName, isBaseLook, isCharacterNode } from '../characters/characters'
import { DEFAULT_DURATION, DURATIONS, type Shot } from '../storyboard/shots'

/** 台词所属节点：有视频的镜头连到视频节点，只有画面的镜头连到图片节点。 */
export function shotTarget(shot: Shot): StoredNode {
  return shot.video ?? shot.main
}

function orderOf(node: StoredNode): number | null {
  const value = (node.data as AudioData).order
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 同一镜头里的先后：data.order 升序，没有 order 的按位置 y、x 排在后面。 */
function byOrder(a: StoredNode, b: StoredNode): number {
  const oa = orderOf(a)
  const ob = orderOf(b)
  if (oa !== null && ob !== null && oa !== ob) return oa - ob
  if (oa !== null && ob === null) return -1
  if (oa === null && ob !== null) return 1
  return a.position.y - b.position.y || a.position.x - b.position.x
}

/** 某个镜头的台词节点（已排好先后）。 */
export function linesOfShot(shot: Shot, nodes: StoredNode[], edges: StoredEdge[]): StoredNode[] {
  const target = shotTarget(shot).id
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const found = new Map<string, StoredNode>()
  for (const edge of edges) {
    if (edge.target !== target || edge.targetHandle !== 'voice') continue
    const source = byId.get(edge.source)
    if (source?.type === 'audio') found.set(source.id, source)
  }
  return [...found.values()].sort(byOrder)
}

/** 说话人：空视为旁白。 */
export function speakerName(node: StoredNode): string {
  return String((node.data as AudioData).speaker || '').trim() || NARRATOR
}

export type LineState = 'done' | 'running' | 'failed' | 'empty' | 'needs_voice' | 'todo'

export interface VoiceLine {
  node: StoredNode
  speaker: string
  /** 实际用于生成的台词：prompt 边上的文本节点内容优先，否则是节点自己的 data.text（与 resolveInputs 一致） */
  text: string
  /** 台词来自连入的文本节点时，这些文本节点的 id（只有一个时才能在这里编辑，改的就是它） */
  linkedTextIds: string[]
  /** 所属镜头；没有 voice 边的台词为 null（未归属镜头） */
  shot: Shot | null
  /** 镜头里的第几句（从 0 开始） */
  order: number
  /** 节点自己的音色，否则说话人角色的音色；空 = 还没选 */
  voice: string
  state: LineState
  /** 选用的配音结果 */
  result?: NodeResult
  /** 配音时长（秒，保留一位小数，只用于显示） */
  seconds: number | null
  /** 配音真实时长（毫秒） */
  ms: number | null
  /** 配音比镜头视频长多少秒（只有视频镜头、且超出时有值；按毫秒原值判断，显示至少 0.1） */
  overBy: number | null
  error: string
}

const round1 = (value: number) => Math.round(value * 10) / 10

/** 配音比镜头视频长多少秒：没有视频节点、没有配音结果、没超出都返回 null。按毫秒原值比较（6.04 秒也算超过 6 秒）。 */
export function overrun(shot: Shot, ms: number | null): number | null {
  if (!shot.video || ms === null) return null
  const videoMs = (Number((shot.video.data as VideoData).duration) || DEFAULT_DURATION) * 1000
  return ms > videoMs ? Math.max(0.1, round1((ms - videoMs) / 1000)) : null
}

/** 台词的实际文本：有 prompt 边时取文本节点内容（和 resolveInputs 一致），否则是 data.text。 */
function lineText(node: StoredNode, nodes: StoredNode[], edges: StoredEdge[]): { text: string; linkedTextIds: string[] } {
  const byId = new Map(nodes.map((item) => [item.id, item]))
  const linked = edges
    .filter((edge) => edge.target === node.id && edge.targetHandle === 'prompt')
    .map((edge) => byId.get(edge.source))
    .filter((item): item is StoredNode => !!item && !!String((item.data as TextData).text || '').trim())
  if (linked.length === 0) return { text: String((node.data as AudioData).text || '').trim(), linkedTextIds: [] }
  return { text: linked.map((item) => String((item.data as TextData).text).trim()).join('\n'), linkedTextIds: linked.map((item) => item.id) }
}

/** 全部台词：按镜头顺序、镜头内按先后；最后是没有 voice 边的“未归属镜头”台词（shot 为 null）。 */
export function buildLines(shots: Shot[], nodes: StoredNode[], edges: StoredEdge[], runs: CanvasRuns | null): VoiceLine[] {
  const lines: VoiceLine[] = []
  const make = (node: StoredNode, shot: Shot | null, order: number) => {
    const data = node.data as AudioData
    const speaker = speakerName(node)
    const { text, linkedTextIds } = lineText(node, nodes, edges)
    const job = runs?.nodes[node.id]?.jobs[0]
    const result = selectedResult(node, runs)
    const voice = String(data.voice || '').trim() || speakerVoice(nodes, edges, speaker)
    const ms = result?.duration_ms || null
    let state: LineState
    if (job && ACTIVE.has(job.status)) state = 'running'
    else if (!text) state = 'empty'
    else if (result) state = 'done'
    else if (job?.status === 'failed') state = 'failed'
    else if (!voice) state = 'needs_voice'
    else state = 'todo'
    lines.push({
      node, speaker, text, linkedTextIds, shot, order, voice, state, result, ms, seconds: ms ? round1(ms / 1000) : null,
      overBy: state === 'done' && shot ? overrun(shot, ms) : null,
      error: job?.status === 'failed' ? job.error : '',
    })
  }
  const placed = new Set<string>()
  for (const shot of shots) {
    linesOfShot(shot, nodes, edges).forEach((node, order) => { placed.add(node.id); make(node, shot, order) })
  }
  nodes.filter((node) => node.type === 'audio' && !placed.has(node.id)).sort(byOrder).forEach((node, order) => make(node, null, order))
  return lines
}

export interface Speaker {
  name: string
  isNarrator: boolean
  /** 角色的基础造型节点；旁白和不在角色里的说话人没有 */
  base?: StoredNode
  /** 这个说话人当前的音色；空 = 还没选 */
  voice: string
  count: number
}

/** 角色的基础造型节点：look 为空 / “默认”的第一个，没有就取第一个。 */
function baseOf(name: string, nodes: StoredNode[], edges: StoredEdge[]): StoredNode | undefined {
  const looks = nodes.filter((node) => isCharacterNode(node, edges) && characterName(node) === name)
  return looks.find(isBaseLook) ?? looks[0]
}

/** 本集说话人（只列出现过台词的）：先角色（按首次出现的顺序），旁白排最后。 */
export function buildSpeakers(all: VoiceLine[], nodes: StoredNode[], edges: StoredEdge[]): Speaker[] {
  const lines = all.filter((line) => line.state !== 'empty')  // 没有台词文字的空节点不算说话
  const order: string[] = []
  const counts = new Map<string, number>()
  for (const line of lines) {
    if (!counts.has(line.speaker)) order.push(line.speaker)
    counts.set(line.speaker, (counts.get(line.speaker) || 0) + 1)
  }
  order.sort((a, b) => Number(a === NARRATOR) - Number(b === NARRATOR))
  return order.map((name) => {
    const isNarrator = name === NARRATOR
    const base = isNarrator ? undefined : baseOf(name, nodes, edges)
    const own = lines.filter((line) => line.speaker === name)
    // 角色看基础造型上的音色；旁白（以及没有角色节点的说话人）看自己各句上设的音色
    const voice = base
      ? String((base.data as { voice?: string }).voice || '').trim()
      : own.map((line) => String((line.node.data as AudioData).voice || '').trim()).find(Boolean) || ''
    return { name, isNarrator, base, voice, count: counts.get(name) || 0 }
  })
}

/** 给说话人选音色要写回哪些节点：角色写基础造型节点，旁白（以及没有角色节点的说话人）写他所有的配音节点。 */
export function voicePatch(speaker: Speaker, lines: VoiceLine[], voice: string): Record<string, string> {
  if (speaker.base) return { [speaker.base.id]: voice }
  return Object.fromEntries(lines.filter((line) => line.speaker === speaker.name).map((line) => [line.node.id, voice]))
}

/**
 * “延长镜头”能把视频调到几秒：不小于配音真实时长（按毫秒比较）的最小允许值；
 * 允许值 = 分镜页时长下拉的选项，再按所选视频模型的 min / max 时长收窄。没有合适的返回 null（让用户缩短台词）。
 */
export function extendedDuration(ms: number, limits: { min?: number; max?: number } = {}): number | null {
  const allowed = DURATIONS.filter((item) => item >= (limits.min ?? 0) && item <= (limits.max ?? Infinity))
  return allowed.find((item) => item * 1000 >= ms) ?? null
}

export const stateLabel: Record<LineState, string> = {
  done: '已生成', running: '生成中', failed: '失败', empty: '台词为空', needs_voice: '待选音色', todo: '未生成',
}

/** 还没配好的台词数（含生成中 / 失败 / 待选音色）。完全没有台词文字的空节点不算一句台词。 */
export function pendingCount(lines: VoiceLine[]): number {
  return lines.filter((line) => line.state !== 'done' && line.state !== 'empty').length
}

/** 把一句未归属的台词归到某个镜头：连 voice 边，order 排在该镜头最后。 */
export function assignLine(line: StoredNode, shot: Shot, existing: StoredNode[]): { edge: StoredEdge; order: number } {
  const target = shotTarget(shot)
  const orders = existing.map((node) => orderOf(node)).filter((value): value is number => value !== null)
  return {
    order: orders.length ? Math.max(...orders) + 1 : existing.length,
    edge: { id: edgeId(line.id, target.id, 'voice'), source: line.id, target: target.id, sourceHandle: 'out', targetHandle: 'voice' },
  }
}

// ── 分镜页“台词”编辑：改写 graph ──

/** 新增一句台词：节点放在该镜头视频（或图片）节点右侧，连 voice 边，order 排在最后。 */
export function addLine(shot: Shot, existing: StoredNode[], speaker: string): { node: StoredNode; edge: StoredEdge } {
  const target = shotTarget(shot)
  const id = newId('audio')
  const orders = existing.map((node) => orderOf(node)).filter((value): value is number => value !== null)
  const order = orders.length ? Math.max(...orders) + 1 : existing.length
  const node: StoredNode = {
    id, type: 'audio',
    position: { x: target.position.x + 420, y: target.position.y + existing.length * 230 },
    data: { label: `台词 · ${speaker}`, speaker, text: '', voice: '', order },
  }
  return { node, edge: { id: edgeId(id, target.id, 'voice'), source: id, target: target.id, sourceHandle: 'out', targetHandle: 'voice' } }
}

/** 重排一句台词（from → to），返回 节点 id → 新 order（重写 0..n-1）。 */
export function reorderLines(lines: StoredNode[], from: number, to: number): Record<string, number> {
  if (from < 0 || from >= lines.length || to < 0 || to >= lines.length) return {}
  const ids = lines.map((node) => node.id)
  const [moved] = ids.splice(from, 1)
  ids.splice(to, 0, moved)
  return Object.fromEntries(ids.map((id, index) => [id, index]))
}
