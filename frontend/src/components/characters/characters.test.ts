import { describe, expect, it } from 'vitest'
import type { CanvasRuns, NodeJob, StoredEdge, StoredNode } from '../../api/nodeCanvas'
import {
  buildCharacters, characterGate, characterName, editCharacter, isCharacterNode, lookName, nameTaken, newCharacterNode, newLookNode, splitCandidates,
} from './characters'

const node = (id: string, data: Record<string, unknown> = {}, x = 0, y = 0, type: StoredNode['type'] = 'image'): StoredNode => ({ id, type, position: { x, y }, data })
const edge = (source: string, target: string, targetHandle: string): StoredEdge => ({ id: `${source}-${target}`, source, target, sourceHandle: 'out', targetHandle })
const job = (status: string, progress = 0, error = ''): NodeJob => ({ id: 'j', status, progress, queue_position: 0, error, model: '', created_at: null })
const runs = (entries: Record<string, { results?: string[]; jobs?: NodeJob[] }>): CanvasRuns => ({
  nodes: Object.fromEntries(Object.entries(entries).map(([id, value]) => [id, {
    jobs: value.jobs || [], results: (value.results || []).map((artifact_id) => ({ artifact_id, type: 'image', url: `/api/artifacts/${artifact_id}/download` })),
  }])),
  active_jobs: 0,
  spent: 0,
})

describe('character recognition', () => {
  it('recognises role=character nodes and legacy reference-only images', () => {
    const nodes = [
      node('a', { role: 'character', character: '狐妖' }),
      node('b', { label: '角色 · 书生' }),  // 旧画布：只有 reference 出边
      node('c', { label: '镜头' }),  // 有 first_frame 出边：不是角色
      node('d', { label: '孤立图' }),  // 没有出边也没有 role：不是角色
      node('t', { text: 'x' }, 0, 0, 'text'),
    ]
    const edges = [edge('b', 'c', 'reference'), edge('c', 'v', 'first_frame'), edge('d', 'c', 'reference'), edge('d', 'v', 'first_frame')]
    expect(nodes.map((item) => isCharacterNode(item, edges))).toEqual([true, true, false, false, false])
  })

  it('takes the name from data.character, else from the label', () => {
    expect(characterName(node('a', { character: '狐妖', label: '别的' }))).toBe('狐妖')
    expect(characterName(node('b', { label: '角色 · 书生' }))).toBe('书生')
    expect(characterName(node('c', { label: '角色·老和尚' }))).toBe('老和尚')
    expect(lookName(node('d', { look: '' }))).toBe('默认')
    expect(lookName(node('e', { look: ' 冬装 ' }))).toBe('冬装')
  })

  it('groups looks under one character with the base look first', () => {
    const nodes = [
      node('w', { role: 'character', character: '狐妖', look: '冬装' }),
      node('b', { role: 'character', character: '狐妖', look: '', prompt: '白发', library_asset_id: 'lib1' }),
      node('s', { role: 'character', character: '书生' }),
    ]
    const result = buildCharacters(nodes, [], null)
    expect(result.map((item) => item.name)).toEqual(['狐妖', '书生'])
    expect(result[0].looks.map((look) => look.name)).toEqual(['默认', '冬装'])
    expect(result[0]).toMatchObject({ description: '白发', libraryAssetId: 'lib1', finalized: false })
  })
})

describe('character state and characterGate', () => {
  const nodes = [
    node('fox', { role: 'character', character: '狐妖', selected: 'f2' }),
    node('scholar', { role: 'character', character: '书生' }),
    node('monk', { role: 'character', character: '老和尚' }),
    node('bad', { role: 'character', character: '路人', selected: 'ghost' }),
  ]
  const value = runs({
    fox: { results: ['f1', 'f2'] },
    scholar: { results: ['s1', 's2'] },
    monk: { jobs: [job('running', 40)] },
    bad: { results: ['x1'] },
  })

  it('derives final / pick / running / empty per character', () => {
    const states = Object.fromEntries(buildCharacters(nodes, [], value).map((item) => [item.name, item.base.state]))
    expect(states).toEqual({ 狐妖: 'final', 书生: 'pick', 老和尚: 'running', 路人: 'pick' })
    const monk = buildCharacters(nodes, [], value)[2]
    expect(monk.base.progress).toBe(40)
    expect(buildCharacters([node('n', { role: 'character', character: '新人' })], [], null)[0].base.state).toBe('empty')
  })

  it('shows a failed job only when there is no final image', () => {
    const failed = runs({ a: { jobs: [job('failed', 0, '超时')] }, b: { results: ['b1'], jobs: [job('failed')] } })
    const list = buildCharacters([node('a', { role: 'character', character: 'A' }), node('b', { role: 'character', character: 'B', selected: 'b1' })], [], failed)
    expect(list.map((item) => item.base.state)).toEqual(['failed', 'final'])
    expect(list[0].base.job?.error).toBe('超时')
  })

  it('gates on the base look being finalized with one of its own results', () => {
    expect(characterGate({ nodes, edges: [] }, value)).toEqual({ done: false, pending: ['书生', '老和尚', '路人'] })
    // 选中的图不是该节点的结果 => 不算定稿
    expect(characterGate({ nodes: [node('bad', { role: 'character', character: '路人', selected: 'ghost' })] }, value).done).toBe(false)
    expect(characterGate({ nodes: [nodes[0]] }, value)).toEqual({ done: true, pending: [] })
    // 没有角色：不挡住下一步
    expect(characterGate({ nodes: [], edges: [] }, null)).toEqual({ done: true, pending: [] })
    // 只有非基础造型定稿不算：看基础造型
    const looks = [node('base', { role: 'character', character: '甲' }), node('w', { role: 'character', character: '甲', look: '冬装', selected: 'w1' })]
    expect(characterGate({ nodes: looks }, runs({ w: { results: ['w1'] } })).pending).toEqual(['甲'])
  })
})

describe('building and editing', () => {
  it('splitCandidates puts the latest batch first and keeps the rest as earlier', () => {
    const results = ['1', '2', '3', '4', '5', '6'].map((artifact_id) => ({ artifact_id, type: 'image', url: '' }))
    const { latest, earlier } = splitCandidates(results)
    expect(latest.map((item) => item.artifact_id)).toEqual(['1', '2', '3', '4'])
    expect(earlier.map((item) => item.artifact_id)).toEqual(['5', '6'])
  })

  it('creates a character node below the existing ones with 4 candidates', () => {
    const existing = [node('a', { role: 'character', character: '狐妖' }, 10, 0), node('b', { role: 'character', character: '书生' }, 30, 380)]
    const created = newCharacterNode(existing, [], '老和尚', '光头')
    expect(created.position).toEqual({ x: 10, y: 760 })
    expect(created.data).toMatchObject({ role: 'character', character: '老和尚', look: '', count: 4, label: '角色 · 老和尚' })
    expect(String(created.data.prompt)).toContain('光头')
  })

  it('creates a look as a same-name character node: base description plus the difference', () => {
    const nodes = [node('a', { role: 'character', character: '狐妖', prompt: '白发金瞳', library_asset_id: 'lib1' }, 5, 0)]
    const fox = buildCharacters(nodes, [], null)[0]
    const created = newLookNode(nodes, [], fox, '冬装', '披黑色斗篷')
    expect(created.data).toMatchObject({ role: 'character', character: '狐妖', look: '冬装', prompt: '白发金瞳，造型：披黑色斗篷', count: 4, label: '角色 · 狐妖 · 冬装' })
    expect(created.position.x).toBe(5)
    expect(created.position.y).toBeGreaterThan(0)
    expect(buildCharacters([...nodes, created], [], null)[0].looks.map((look) => look.name)).toEqual(['默认', '冬装'])
  })

  it('renames every look node of the character (character and label) and only edits the base prompt', () => {
    const nodes = [
      node('a', { role: 'character', character: '狐妖', label: '角色 · 狐妖', prompt: '旧' }),
      node('w', { role: 'character', character: '狐妖', look: '冬装', label: '角色 · 狐妖 · 冬装', prompt: '旧，造型：斗篷' }),
      node('s', { role: 'character', character: '书生', label: '角色 · 书生' }),
    ]
    const fox = buildCharacters(nodes, [], null)[0]
    const next = editCharacter(nodes, fox, { name: '九尾', description: '新描述' })
    expect(next[0].data).toMatchObject({ character: '九尾', label: '角色 · 九尾', prompt: '新描述' })
    expect(next[1].data).toMatchObject({ character: '九尾', label: '角色 · 九尾 · 冬装', prompt: '旧，造型：斗篷' })
    expect(next[2]).toBe(nodes[2])
  })

  it('detects a taken name except the character being edited', () => {
    const nodes = [node('a', { role: 'character', character: '狐妖' }), node('s', { role: 'character', character: '书生' })]
    expect(nameTaken(nodes, [], '书生')).toBe(true)
    expect(nameTaken(nodes, [], ' 书生 ', '狐妖')).toBe(true)
    expect(nameTaken(nodes, [], '狐妖', '狐妖')).toBe(false)
    expect(nameTaken(nodes, [], '新人')).toBe(false)
  })
})
