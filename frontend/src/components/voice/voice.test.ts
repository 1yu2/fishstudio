import { describe, expect, it } from 'vitest'
import type { CanvasRuns, StoredEdge, StoredNode } from '../../api/nodeCanvas'
import { buildShots } from '../storyboard/shots'
import { addLine, assignLine, buildLines, buildSpeakers, extendedDuration, linesOfShot, overrun, pendingCount, reorderLines, voicePatch } from './voice'

const node = (id: string, type: StoredNode['type'], data: Record<string, unknown> = {}, y = 0): StoredNode => ({ id, type, position: { x: 0, y }, data })
const edge = (source: string, target: string, targetHandle: string): StoredEdge => ({ id: `${source}-${target}-${targetHandle}`, source, target, sourceHandle: 'out', targetHandle })
const job = (status: string, error = '') => ({ id: 'j', status, progress: 0, queue_position: 0, error, model: '', created_at: null })
const audioResult = (artifact_id: string, duration_ms: number) => ({ artifact_id, type: 'audio', url: `/api/artifacts/${artifact_id}/download`, duration_ms })

const nodes = (): StoredNode[] => [
  node('fox', 'image', { role: 'character', character: '狐妖', voice: 'Serena' }, -400),
  node('scholar', 'image', { role: 'character', character: '书生' }, -300),
  node('v1', 'video', { duration: 4, order: 0 }, 0),
  node('v2', 'video', { duration: 5, order: 1 }, 400),
  node('a1', 'audio', { speaker: '狐妖', text: '你来了', order: 1 }, 0),
  node('a0', 'audio', { speaker: '书生', text: '姑娘', order: 0 }, 100),
  node('a2', 'audio', { speaker: '旁白', text: '那一夜', voice: 'Kai' }, 0),
  node('a3', 'audio', { speaker: '狐妖', text: '   ' }, 200),
]
const edges = (): StoredEdge[] => [
  edge('a1', 'v1', 'voice'), edge('a0', 'v1', 'voice'), edge('a2', 'v2', 'voice'), edge('a3', 'v2', 'voice'),
]
const runsOf = (map: CanvasRuns['nodes']): CanvasRuns => ({ nodes: map, active_jobs: 0, spent: 0 })

describe('lines per shot', () => {
  it('collects voice-edge audio per shot and orders by data.order, then position', () => {
    const shots = buildShots(nodes(), edges(), null)
    expect(linesOfShot(shots[0], nodes(), edges()).map((item) => item.id)).toEqual(['a0', 'a1'])
    expect(linesOfShot(shots[1], nodes(), edges()).map((item) => item.id)).toEqual(['a2', 'a3'])
  })

  it('attaches lines of an image-only shot to the image node', () => {
    const list = [node('k', 'image', { label: '1 · 远景' }), node('l', 'audio', { speaker: '旁白', text: '天亮了' })]
    const shots = buildShots(list, [edge('l', 'k', 'voice')], null)
    expect(linesOfShot(shots[0], list, [edge('l', 'k', 'voice')]).map((item) => item.id)).toEqual(['l'])
  })
})

describe('line state', () => {
  const lineStates = (runs: CanvasRuns | null) => {
    const shots = buildShots(nodes(), edges(), runs)
    return Object.fromEntries(buildLines(shots, nodes(), edges(), runs).map((line) => [line.node.id, line.state]))
  }

  it('derives todo / needs_voice / empty without any result', () => {
    // a0 书生没有音色、a1 狐妖用角色音色、a2 自带音色、a3 空台词
    expect(lineStates(null)).toEqual({ a0: 'needs_voice', a1: 'todo', a2: 'todo', a3: 'empty' })
  })

  it('derives running, failed and done from jobs and results', () => {
    const states = lineStates(runsOf({
      a0: { jobs: [job('running')], results: [] },
      a1: { jobs: [job('succeeded')], results: [audioResult('r1', 2800)] },
      a2: { jobs: [job('failed', '额度不足')], results: [] },
    }))
    expect(states).toMatchObject({ a0: 'running', a1: 'done', a2: 'failed' })
  })

  it('keeps the effective voice: node override, then the speaker\'s character voice', () => {
    const shots = buildShots(nodes(), edges(), null)
    const lines = buildLines(shots, nodes(), edges(), null)
    expect(Object.fromEntries(lines.map((line) => [line.node.id, line.voice]))).toEqual({ a0: '', a1: 'Serena', a2: 'Kai', a3: 'Serena' })
    expect(pendingCount(lines)).toBe(3)  // 没有台词文字的空节点不算一句
  })
})

describe('overrun', () => {
  it('reports how many seconds the dubbing is longer than the shot video', () => {
    const shots = buildShots(nodes(), edges(), null)
    expect(overrun(shots[0], 5200)).toBe(1.2)
    expect(overrun(shots[0], 4000)).toBeNull()
    expect(overrun(shots[0], 4040)).toBe(0.1)  // 按毫秒原值判断：4.04 秒也比 4 秒长
    expect(overrun(shots[0], null)).toBeNull()
    const runs = runsOf({ a1: { jobs: [], results: [audioResult('r1', 5200)] } })
    const lines = buildLines(buildShots(nodes(), edges(), runs), nodes(), edges(), runs)
    expect(lines.find((line) => line.node.id === 'a1')).toMatchObject({ seconds: 5.2, overBy: 1.2 })
    expect(lines.find((line) => line.node.id === 'a0')?.overBy).toBeNull()
  })

  it('ignores shots without a video node', () => {
    const list = [node('k', 'image', {}), node('l', 'audio', { text: '天亮了' })]
    expect(overrun(buildShots(list, [edge('l', 'k', 'voice')], null)[0], 9000)).toBeNull()
  })

  it('extends a shot to the smallest allowed duration that fits the real length', () => {
    expect(extendedDuration(5200)).toBe(6)
    expect(extendedDuration(6000)).toBe(6)
    expect(extendedDuration(6040)).toBe(7)  // 6.04 秒按大于 6 秒处理
    expect(extendedDuration(12400)).toBeNull()
    expect(extendedDuration(1100)).toBe(2)
  })

  it('respects the duration range of the selected video model', () => {
    expect(extendedDuration(3000, { min: 4, max: 8 })).toBe(4)
    expect(extendedDuration(9000, { min: 4, max: 8 })).toBeNull()
    expect(extendedDuration(6040, { max: 6 })).toBeNull()
  })
})

describe('speakers', () => {
  it('lists only speakers that have lines: characters first, narrator last', () => {
    const shots = buildShots(nodes(), edges(), null)
    const lines = buildLines(shots, nodes(), edges(), null)
    const speakers = buildSpeakers(lines, nodes(), edges())
    expect(speakers.map((item) => [item.name, item.isNarrator, item.voice, item.count])).toEqual([
      ['书生', false, '', 1], ['狐妖', false, 'Serena', 1], ['旁白', true, 'Kai', 1],
    ])
  })

  it('writes a character voice to the base look and a narrator voice to every narrator line', () => {
    const list = [...nodes(), node('a4', 'audio', { speaker: '旁白', text: '又一句' }, 300)]
    const links = [...edges(), edge('a4', 'v1', 'voice')]
    const lines = buildLines(buildShots(list, links, null), list, links, null)
    const speakers = buildSpeakers(lines, list, links)
    const narrator = speakers.find((item) => item.isNarrator)!
    expect(voicePatch(narrator, lines, 'Moon')).toEqual({ a2: 'Moon', a4: 'Moon' })
    expect(voicePatch(speakers.find((item) => item.name === '书生')!, lines, 'Ethan')).toEqual({ scholar: 'Ethan' })
  })

  it('picks the base look when a character has several looks', () => {
    const list = [
      node('base', 'image', { role: 'character', character: '狐妖' }),
      node('winter', 'image', { role: 'character', character: '狐妖', look: '冬装' }),
      node('v', 'video', {}),
      node('l', 'audio', { speaker: '狐妖', text: '你好' }),
    ]
    const links = [edge('l', 'v', 'voice')]
    const lines = buildLines(buildShots(list, links, null), list, links, null)
    expect(voicePatch(buildSpeakers(lines, list, links)[0], lines, 'Cherry')).toEqual({ base: 'Cherry' })
  })
})

describe('line text and scope', () => {
  it('takes the text of a linked text node, like the generator does, and remembers which node it is', () => {
    const list = [node('t', 'text', { text: ' 连入的台词 ' }), node('v', 'video'), node('l', 'audio', { speaker: '旁白', text: '' })]
    const links = [edge('l', 'v', 'voice'), edge('t', 'l', 'prompt')]
    const [line] = buildLines(buildShots(list, links, null), list, links, null)
    expect(line).toMatchObject({ text: '连入的台词', linkedTextIds: ['t'], state: 'needs_voice' })
  })

  it('lists audio nodes without a voice edge after the shot lines, with no shot', () => {
    const list = [...nodes(), node('loose', 'audio', { speaker: '旁白', text: '没人认领' }, 500)]
    const lines = buildLines(buildShots(list, edges(), null), list, edges(), null)
    expect(lines.map((item) => item.node.id)).toEqual(['a0', 'a1', 'a2', 'a3', 'loose'])
    expect(lines[4]).toMatchObject({ shot: null, state: 'needs_voice' })
  })

  it('assigns a loose line to a shot with a voice edge, ordered last', () => {
    const list = [...nodes(), node('loose', 'audio', { speaker: '旁白', text: '没人认领' }, 500)]
    const shots = buildShots(list, edges(), null)
    const assigned = assignLine(list.find((item) => item.id === 'loose')!, shots[0], linesOfShot(shots[0], list, edges()))
    expect(assigned.order).toBe(2)
    expect(assigned.edge).toMatchObject({ source: 'loose', target: 'v1', targetHandle: 'voice' })
  })
})

describe('editing helpers', () => {
  it('adds a line to the end with a voice edge to the shot video', () => {
    const shots = buildShots(nodes(), edges(), null)
    const existing = linesOfShot(shots[0], nodes(), edges())
    const created = addLine(shots[0], existing, '狐妖')
    expect(created.node).toMatchObject({ type: 'audio', data: { speaker: '狐妖', text: '', order: 2 } })
    expect(created.edge).toMatchObject({ source: created.node.id, target: 'v1', targetHandle: 'voice' })
    expect(addLine(shots[0], [], '旁白').node.data.order).toBe(0)
  })

  it('reorders by rewriting 0..n-1', () => {
    const list = [node('a', 'audio'), node('b', 'audio'), node('c', 'audio')]
    expect(reorderLines(list, 0, 2)).toEqual({ b: 0, c: 1, a: 2 })
    expect(reorderLines(list, 0, 5)).toEqual({})
  })
})
