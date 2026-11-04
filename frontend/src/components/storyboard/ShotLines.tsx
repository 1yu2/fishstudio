import { useMemo } from 'react'
import { ChevronDown, ChevronUp, Plus, Trash2 } from 'lucide-react'
import { NARRATOR } from '../nodeCanvas/graph'
import { characterName, isCharacterNode } from '../characters/characters'
import { addLine, linesOfShot, reorderLines, speakerName } from '../voice/voice'
import type { Shot } from './shots'
import type { Storyboard } from './useStoryboard'

const MAX_LINE_CHARS = 500
const LABEL_PREFIX = '台词 · '

/**
 * 分镜页的“台词”：本镜头的配音节点列表，可添加、删除、排序、改说话人和文字（写回 graph）。
 * 只编辑台词，不在这里生成；生成和选音色在配音页。
 */
export default function ShotLines({ shot, sb }: { shot: Shot; sb: Storyboard }) {
  const lines = linesOfShot(shot, sb.nodes, sb.edges)
  // 说话人候选：本集角色（按画布顺序、去重）+ 旁白
  const roles = useMemo(() => {
    const names: string[] = []
    for (const node of sb.nodes) {
      if (!isCharacterNode(node, sb.edges)) continue
      const name = characterName(node)
      if (name && !names.includes(name)) names.push(name)
    }
    return names
  }, [sb.nodes, sb.edges])

  const add = () => {
    const created = addLine(shot, lines, roles[0] ?? NARRATOR)
    sb.update((current) => ({ nodes: [...current.nodes, created.node], edges: [...current.edges, created.edge] }))
  }
  const remove = (id: string) => sb.update((current) => ({
    nodes: current.nodes.filter((node) => node.id !== id),
    edges: current.edges.filter((edge) => edge.source !== id && edge.target !== id),
  }))
  const move = (from: number, to: number) => {
    const patch = reorderLines(lines, from, to)
    if (Object.keys(patch).length === 0) return
    sb.update((current) => ({ ...current, nodes: current.nodes.map((node) => node.id in patch ? { ...node, data: { ...node.data, order: patch[node.id] } } : node) }))
  }
  const rename = (id: string, label: string | undefined, speaker: string) => {
    const keepLabel = !label || label.startsWith(LABEL_PREFIX)
    sb.updateData(id, { speaker, ...(keepLabel ? { label: `${LABEL_PREFIX}${speaker}` } : {}) })
  }

  return (
    <div className="sb-field" role="group" aria-label="台词">
      <span>台词{lines.length > 0 ? ` · ${lines.length} 句` : ''}</span>
      {lines.length === 0 && <span className="sb-muted">这个镜头没有台词；有对白或旁白时点「添加台词」</span>}
      <ul className="sb-lines">
        {lines.map((node, index) => {
          const speaker = speakerName(node)
          const options = roles.includes(speaker) || speaker === NARRATOR ? roles : [...roles, speaker]
          return (
            <li key={node.id} className="sb-line">
              <div className="sb-line__row">
                <select aria-label={`第 ${index + 1} 句说话人`} value={speaker} onChange={(event) => rename(node.id, (node.data as { label?: string }).label, event.target.value)}>
                  {options.map((name) => <option key={name} value={name}>{name}</option>)}
                  <option value={NARRATOR}>{NARRATOR}</option>
                </select>
                <span className="sb-line__tools">
                  <button type="button" className="sb-icon" aria-label={`第 ${index + 1} 句上移`} disabled={index === 0} onClick={() => move(index, index - 1)}><ChevronUp size={14} aria-hidden="true" /></button>
                  <button type="button" className="sb-icon" aria-label={`第 ${index + 1} 句下移`} disabled={index === lines.length - 1} onClick={() => move(index, index + 1)}><ChevronDown size={14} aria-hidden="true" /></button>
                  <button type="button" className="sb-icon" aria-label={`删除第 ${index + 1} 句`} onClick={() => remove(node.id)}><Trash2 size={14} aria-hidden="true" /></button>
                </span>
              </div>
              <textarea aria-label={`第 ${index + 1} 句台词`} rows={2} maxLength={MAX_LINE_CHARS} value={String((node.data as { text?: string }).text || '')} placeholder="台词或旁白"
                onChange={(event) => sb.updateData(node.id, { text: event.target.value })} />
            </li>
          )
        })}
      </ul>
      <button type="button" className="sb-btn sb-btn--add" onClick={add}><Plus size={13} aria-hidden="true" />添加台词</button>
    </div>
  )
}
