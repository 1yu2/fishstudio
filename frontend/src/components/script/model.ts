/**
 * 剧本页的纯函数：大纲统计、时长估算、场 / 块的增删移、拍平成文字、对白过长检查。
 * 不依赖 React，便于单测。所有函数返回新对象，不修改入参。
 */
import type { ScriptAct, ScriptBlock, ScriptPayload, ScriptScene, ScriptSource } from '../../api/script'

export const MAX_ACTS = 20
export const MAX_SCENES = 80
export const LONG_DIALOGUE = 30
export const EMPTY_SCRIPT: ScriptPayload = { acts: [] }

export const SOURCE_LABEL: Record<ScriptSource, string> = {
  user: '手动编辑', ai_draft: 'AI 起草', import: '粘贴导入', restore: '恢复历史版本',
}

const NUMERALS = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十']
/** 第 1 → “一”，11 → “十一”，20 → “二十”（幕最多 20） */
function numeral(value: number): string {
  if (value <= 10) return NUMERALS[value - 1] ?? String(value)
  const tens = Math.floor(value / 10)
  return `${tens > 1 ? NUMERALS[tens - 1] : ''}十${value % 10 ? NUMERALS[(value % 10) - 1] : ''}`
}
export const actTitle = (index: number) => `第${numeral(index + 1)}幕`

const chars = (text: string) => text.replace(/\s+/g, '').length

export function wordCount(payload: ScriptPayload): number {
  return payload.acts.reduce((sum, act) => sum + act.scenes.reduce((inner, scene) => inner + scene.blocks.reduce((total, block) => total + chars(block.text), 0), 0), 0)
}

export function hasContent(payload: ScriptPayload): boolean {
  return payload.acts.some((act) => act.scenes.some((scene) => scene.blocks.some((block) => block.text.trim() !== '')))
}

export const allScenes = (payload: ScriptPayload): ScriptScene[] => payload.acts.flatMap((act) => act.scenes)
export const sceneCount = (payload: ScriptPayload) => allScenes(payload).length

/** 估算时长（秒）：动作每 30 字 2 秒，对白每 4 字 1 秒，取整。 */
export function sceneSeconds(scene: ScriptScene): number {
  let seconds = 0
  for (const block of scene.blocks) seconds += block.type === 'dialogue' ? chars(block.text) / 4 : (chars(block.text) / 30) * 2
  return Math.round(seconds)
}
export const totalSeconds = (payload: ScriptPayload) => allScenes(payload).reduce((sum, scene) => sum + sceneSeconds(scene), 0)

export function dialogueCount(payload: ScriptPayload): number {
  return allScenes(payload).reduce((sum, scene) => sum + scene.blocks.filter((block) => block.type === 'dialogue').length, 0)
}

/** 对白超过 30 字的场：{场序号（从 1 开始）, 句数}，按场顺序。 */
export function longDialogues(payload: ScriptPayload): { sceneNumber: number; count: number }[] {
  const found: { sceneNumber: number; count: number }[] = []
  allScenes(payload).forEach((scene, index) => {
    const count = scene.blocks.filter((block) => block.type === 'dialogue' && chars(block.text) > LONG_DIALOGUE).length
    if (count > 0) found.push({ sceneNumber: index + 1, count })
  })
  return found
}

/** 底栏提示：第一个有超长对白的场；没有则返回空串。 */
export function longDialogueHint(payload: ScriptPayload): string {
  const [first, ...rest] = longDialogues(payload)
  if (!first) return ''
  const more = rest.length > 0 ? `（另有 ${rest.length} 场）` : ''
  return `场 ${first.sceneNumber} 有 ${first.count} 句对白超过 ${LONG_DIALOGUE} 字，配音会偏长${more}`
}

export function characterNames(payload: ScriptPayload): string[] {
  const names = new Set<string>()
  for (const scene of allScenes(payload)) for (const block of scene.blocks) if (block.type === 'dialogue' && block.character.trim()) names.add(block.character.trim())
  return [...names]
}

/** 拆分镜头接口（copilot/split）接受的想法最大字数；超过时由调用方阻止，不静默截断。 */
export const SPLIT_IDEA_LIMIT = 20_000

/** 拍平成文字（幕 / 场标题 + heading + 动作 + “角色：台词”），作为拆分镜头的想法，也用于历史预览；不截断。 */
export function flattenScript(payload: ScriptPayload): string {
  const lines: string[] = []
  let number = 0
  for (const act of payload.acts) {
    if (act.title.trim()) lines.push(act.title.trim())
    for (const scene of act.scenes) {
      number += 1
      lines.push(`场 ${number}${scene.title.trim() ? ` ${scene.title.trim()}` : ''}`)
      if (scene.heading.trim()) lines.push(scene.heading.trim())
      for (const block of scene.blocks) {
        const text = block.text.trim()
        if (!text) continue
        lines.push(block.type === 'dialogue' && block.character.trim() ? `${block.character.trim()}：${text}` : text)
      }
    }
  }
  return lines.join('\n')
}

/** 拆分镜头数 = 场数 × 2，夹在 4–12。 */
export const splitShotCount = (scenes: number) => Math.max(4, Math.min(12, scenes * 2))

// ── 编辑 ──

/** 最小的未被占用的 s{n}，保证 payload 内唯一且可预期。 */
export function newSceneId(payload: ScriptPayload): string {
  const used = new Set(allScenes(payload).map((scene) => scene.id))
  let n = 1
  while (used.has(`s${n}`)) n += 1
  return `s${n}`
}

export const blankScene = (payload: ScriptPayload): ScriptScene => ({ id: newSceneId(payload), title: '', heading: '', blocks: [newBlock('action')] })

/** “自己从空白开始”：一幕一场。 */
export function blankScript(): ScriptPayload {
  const payload = EMPTY_SCRIPT
  return { acts: [{ title: actTitle(0), scenes: [blankScene(payload)] }] }
}

export function addScene(payload: ScriptPayload, actIndex: number): { payload: ScriptPayload; sceneId: string } | null {
  if (sceneCount(payload) >= MAX_SCENES) return null
  if (payload.acts.length === 0) {
    const created = blankScript()
    return { payload: created, sceneId: created.acts[0].scenes[0].id }
  }
  const scene = blankScene(payload)
  const index = Math.min(Math.max(actIndex, 0), payload.acts.length - 1)
  return { payload: { acts: payload.acts.map((act, i) => i === index ? { ...act, scenes: [...act.scenes, scene] } : act) }, sceneId: scene.id }
}

export function addAct(payload: ScriptPayload): { payload: ScriptPayload; sceneId: string } | null {
  if (payload.acts.length >= MAX_ACTS || sceneCount(payload) >= MAX_SCENES) return null
  const scene = blankScene(payload)
  return { payload: { acts: [...payload.acts, { title: actTitle(payload.acts.length), scenes: [scene] }] }, sceneId: scene.id }
}

export const removeAct = (payload: ScriptPayload, actIndex: number): ScriptPayload => ({ acts: payload.acts.filter((_, i) => i !== actIndex) })

export const removeScene = (payload: ScriptPayload, sceneId: string): ScriptPayload => ({ acts: payload.acts.map((act) => ({ ...act, scenes: act.scenes.filter((scene) => scene.id !== sceneId) })) })

/** 场上移 / 下移：幕内与邻场交换；到幕边界时移进相邻幕的末尾 / 开头；整份剧本的头尾不动。 */
export function moveScene(payload: ScriptPayload, sceneId: string, direction: -1 | 1): ScriptPayload {
  const actIndex = payload.acts.findIndex((act) => act.scenes.some((scene) => scene.id === sceneId))
  if (actIndex < 0) return payload
  const act = payload.acts[actIndex]
  const at = act.scenes.findIndex((scene) => scene.id === sceneId)
  const scene = act.scenes[at]
  const acts: ScriptAct[] = payload.acts.map((item) => ({ ...item, scenes: [...item.scenes] }))
  const to = at + direction
  if (to >= 0 && to < act.scenes.length) {
    acts[actIndex].scenes.splice(at, 1)
    acts[actIndex].scenes.splice(to, 0, scene)
    return { acts }
  }
  const neighbour = acts[actIndex + direction]
  if (!neighbour) return payload
  acts[actIndex].scenes.splice(at, 1)
  if (direction < 0) neighbour.scenes.push(scene)
  else neighbour.scenes.unshift(scene)
  return { acts }
}

export function canMoveScene(payload: ScriptPayload, sceneId: string, direction: -1 | 1): boolean {
  const next = moveScene(payload, sceneId, direction)
  return next !== payload
}

export const updateAct = (payload: ScriptPayload, actIndex: number, patch: Partial<ScriptAct>): ScriptPayload => ({ acts: payload.acts.map((act, i) => i === actIndex ? { ...act, ...patch } : act) })

export const updateScene = (payload: ScriptPayload, sceneId: string, patch: Partial<ScriptScene>): ScriptPayload => ({
  acts: payload.acts.map((act) => ({ ...act, scenes: act.scenes.map((scene) => scene.id === sceneId ? { ...scene, ...patch } : scene) })),
})

export function updateBlocks(payload: ScriptPayload, sceneId: string, fn: (blocks: ScriptBlock[]) => ScriptBlock[]): ScriptPayload {
  return updateScene(payload, sceneId, { blocks: fn(allScenes(payload).find((scene) => scene.id === sceneId)?.blocks ?? []) })
}

export function newBlockId(): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID().slice(0, 12) : Math.random().toString(36).slice(2, 14)
  return `b-${random}`
}

export const newBlock = (type: ScriptBlock['type']): ScriptBlock => ({ id: newBlockId(), type, character: '', text: '' })

/** 给没有 id（导入 / AI 起草 / 旧数据）或 id 重复的块补上 id；全都合格时原样返回。 */
export function ensureBlockIds(payload: ScriptPayload): ScriptPayload {
  const seen = new Set<string>()
  let changed = false
  const acts = payload.acts.map((act) => ({
    ...act,
    scenes: act.scenes.map((scene) => ({
      ...scene,
      blocks: scene.blocks.map((block) => {
        if (block.id && !seen.has(block.id)) { seen.add(block.id); return block }
        changed = true
        const id = newBlockId()
        seen.add(id)
        return { ...block, id }
      }),
    })),
  }))
  return changed ? { acts } : payload
}

export function insertBlock(blocks: ScriptBlock[], after: number, type: ScriptBlock['type']): ScriptBlock[] {
  return [...blocks.slice(0, after + 1), newBlock(type), ...blocks.slice(after + 1)]
}

export function moveBlock(blocks: ScriptBlock[], index: number, direction: -1 | 1): ScriptBlock[] {
  const to = index + direction
  if (to < 0 || to >= blocks.length) return blocks
  const next = [...blocks]
  ;[next[index], next[to]] = [next[to], next[index]]
  return next
}

/** 把块文字里 [start, end) 这一段替换成新文字；这个位置上的文字已不是原文（期间被改过）时返回 null，不去别处找。 */
export function replaceSelection(text: string, start: number, end: number, original: string, replacement: string): string | null {
  if (text.slice(start, end) !== original) return null
  return text.slice(0, start) + replacement + text.slice(end)
}
