import { describe, expect, it } from 'vitest'
import type { CanvasRuns, StoredEdge, StoredNode } from '../../api/nodeCanvas'
import { canConnect, dropTarget, layoutSplit, resolveInputs } from './graph'

const node = (id: string, type: StoredNode['type'], data: Record<string, unknown> = {}): StoredNode => ({ id, type, position: { x: 0, y: 0 }, data })
const edge = (source: string, target: string, targetHandle: string): StoredEdge => ({ id: `${source}-${target}-${targetHandle}`, source, target, sourceHandle: 'out', targetHandle })
const runs = (results: Record<string, string[]>): CanvasRuns => ({
  nodes: Object.fromEntries(Object.entries(results).map(([id, ids]) => [id, { jobs: [], results: ids.map((artifact_id) => ({ artifact_id, type: 'image', url: `/api/artifacts/${artifact_id}/download` })) }])),
  active_jobs: 0,
  spent: 0,
})

describe('canConnect', () => {
  const nodes = [node('t', 'text'), node('a', 'image'), node('b', 'image'), node('v', 'video')]

  it('only accepts compatible handles', () => {
    expect(canConnect(nodes, [], { source: 't', target: 'a', targetHandle: 'prompt' })).toBe(true)
    expect(canConnect(nodes, [], { source: 't', target: 'a', targetHandle: 'reference' })).toBe(false)
    expect(canConnect(nodes, [], { source: 'a', target: 'v', targetHandle: 'first_frame' })).toBe(true)
    expect(canConnect(nodes, [], { source: 'v', target: 'a', targetHandle: 'reference' })).toBe(false)
    expect(canConnect(nodes, [], { source: 'a', target: 'a', targetHandle: 'reference' })).toBe(false)
  })

  it('allows a single first frame and no duplicate edges', () => {
    const edges = [edge('a', 'v', 'first_frame')]
    expect(canConnect(nodes, edges, { source: 'b', target: 'v', targetHandle: 'first_frame' })).toBe(false)
    expect(canConnect(nodes, edges, { source: 'b', target: 'v', targetHandle: 'last_frame' })).toBe(true)
    expect(canConnect(nodes, [edge('a', 'b', 'reference')], { source: 'a', target: 'b', targetHandle: 'reference' })).toBe(false)
  })
})

describe('resolveInputs', () => {
  it('merges upstream text with the node prompt and uses selected references', () => {
    const nodes = [node('t', 'text', { text: '赛博朋克风格' }), node('ref', 'image', { selected: 'old' }), node('img', 'image', { prompt: '林夏站在雨里', count: 2, model: 'seedream' })]
    const edges = [edge('t', 'img', 'prompt'), edge('ref', 'img', 'reference')]
    const { request, blockers } = resolveInputs(nodes[2], nodes, edges, runs({ ref: ['new', 'old'] }), '9:16')
    expect(blockers).toEqual([])
    expect(request).toEqual({ kind: 'image', prompt: '赛博朋克风格\n林夏站在雨里', model: 'seedream', aspect_ratio: '9:16', count: 2, reference_artifact_ids: ['old'] })
  })

  it('blocks when an upstream frame has no result yet', () => {
    const nodes = [node('k', 'image', { label: '关键帧' }), node('v', 'video', { prompt: '推门', tier: 'final', duration: 6 })]
    const { request, blockers } = resolveInputs(nodes[1], nodes, [edge('k', 'v', 'first_frame')], runs({}), '16:9')
    expect(blockers).toEqual(['首帧节点「关键帧」还没有图片'])
    expect(request).toMatchObject({ kind: 'video', tier: 'final', duration: 6, first_frame_artifact_id: null })
  })

  it('passes first and last frames to video nodes', () => {
    const nodes = [node('a', 'image'), node('b', 'image'), node('v', 'video')]
    const edges = [edge('a', 'v', 'first_frame'), edge('b', 'v', 'last_frame')]
    const { request, blockers } = resolveInputs(nodes[2], nodes, edges, runs({ a: ['fa'], b: ['fb'] }), '9:16')
    expect(blockers).toEqual([])
    expect(request).toMatchObject({ first_frame_artifact_id: 'fa', last_frame_artifact_id: 'fb', tier: 'preview', duration: 5 })
  })

  it('requires a prompt for images', () => {
    expect(resolveInputs(node('i', 'image'), [node('i', 'image')], [], null, '9:16').blockers).toEqual(['写提示词，或连入一个文本节点'])
  })
})

describe('dropTarget', () => {
  it('creates downstream and upstream nodes for loose drags', () => {
    expect(dropTarget('text', 'source', 'out')).toEqual({ type: 'image', handle: 'prompt', reverse: false })
    expect(dropTarget('image', 'source', 'out')).toEqual({ type: 'video', handle: 'first_frame', reverse: false })
    expect(dropTarget('video', 'target', 'last_frame')).toEqual({ type: 'image', handle: 'last_frame', reverse: true })
    expect(dropTarget('image', 'target', 'prompt')).toEqual({ type: 'text', handle: 'prompt', reverse: true })
    expect(dropTarget('video', 'source', null)).toBeNull()
  })
})

describe('layoutSplit', () => {
  it('lays characters, keyframes and videos in columns below existing nodes', () => {
    const { nodes, edges } = layoutSplit({
      characters: [{ name: '林夏', description: '短发' }],
      shots: [
        { title: '推门', prompt: '林夏推门进店', characters: ['林夏', '路人'], duration_s: 4 },
        { title: '', prompt: '空镜', characters: [], duration_s: 3 },
      ],
    }, [node('old', 'text')])
    expect(nodes.map((item) => item.type)).toEqual(['image', 'image', 'video', 'image', 'video'])
    expect(nodes[0].data.label).toBe('角色 · 林夏')
    expect(nodes[2].data).toMatchObject({ duration: 4, tier: 'preview', label: '1 · 推门' })
    expect(nodes[3].data.label).toBe('2 · 镜头 2 关键帧')
    expect(Math.min(...nodes.map((item) => item.position.y))).toBeGreaterThan(0)
    expect(edges.map((item) => item.targetHandle)).toEqual(['reference', 'first_frame', 'first_frame'])
    expect(edges[0]).toMatchObject({ source: nodes[0].id, target: nodes[1].id })
  })
})
