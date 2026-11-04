import { describe, expect, it } from 'vitest'
import type { CanvasRuns, StoredEdge, StoredNode } from '../../api/nodeCanvas'
import fixture from '../../../../backend/tests/fixtures/node_canvas_resolve_cases.json'
import { MAX_VIDEO_REFERENCES, REFERENCE_MISSING, REFERENCE_WITH_FRAME, canConnect, defaultData, dropTarget, label, layoutSplit, resolveInputs, speakerVoice, statusText } from './graph'

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

describe('canConnect: character reference images into video', () => {
  const chars = Array.from({ length: 10 }, (_, index) => node(`c${index}`, 'image', { role: 'character' }))
  const nodes = [...chars, node('t', 'text'), node('v', 'video'), node('i', 'image')]

  it('accepts several image -> video reference edges, up to the video limit', () => {
    expect(canConnect(nodes, [], { source: 'c0', target: 'v', targetHandle: 'reference' })).toBe(true)
    expect(canConnect(nodes, [edge('c0', 'v', 'reference')], { source: 'c1', target: 'v', targetHandle: 'reference' })).toBe(true)
    const full = chars.slice(0, MAX_VIDEO_REFERENCES).map((item) => edge(item.id, 'v', 'reference'))
    expect(canConnect(nodes, full, { source: 'c9', target: 'v', targetHandle: 'reference' })).toBe(false)
    expect(MAX_VIDEO_REFERENCES).toBe(9)
  })

  it('keeps the image-node limit at 4 and rejects non-image sources', () => {
    const four = chars.slice(0, 4).map((item) => edge(item.id, 'i', 'reference'))
    expect(canConnect(nodes, four, { source: 'c5', target: 'i', targetHandle: 'reference' })).toBe(false)
    expect(canConnect(nodes, [], { source: 't', target: 'v', targetHandle: 'reference' })).toBe(false)
  })
})

describe('resolveInputs: video reference images', () => {
  it('collects reference edges in order, deduplicates, and sends no frames', () => {
    const nodes = [node('a', 'image', { role: 'character', selected: 'old' }), node('b', 'image', { role: 'character' }), node('c', 'image', { role: 'character' }), node('v', 'video', { prompt: '对视' })]
    const edges = [edge('a', 'v', 'reference'), edge('b', 'v', 'reference'), edge('c', 'v', 'reference')]
    const { request, blockers } = resolveInputs(nodes[3], nodes, edges, runs({ a: ['new', 'old'], b: ['old'], c: ['rc'] }), '9:16')
    expect(blockers).toEqual([])
    expect(request).toMatchObject({ kind: 'video', first_frame_artifact_id: null, last_frame_artifact_id: null, reference_artifact_ids: ['old', 'rc'] })
  })

  it('blocks a reference image with no result yet', () => {
    const nodes = [node('a', 'image', { label: '角色 · 狐妖' }), node('v', 'video', { prompt: '对视' })]
    const { blockers } = resolveInputs(nodes[1], nodes, [edge('a', 'v', 'reference')], runs({}), '9:16')
    expect(blockers).toEqual(['参考图节点「角色 · 狐妖」还没有图片'])
  })

  it('blocks a parked first frame with no reference images and no first frame', () => {
    const nodes = [node('k', 'image'), node('v', 'video', { prompt: '对视', parked_first_frame: 'k' })]
    expect(resolveInputs(nodes[1], nodes, [], runs({ k: ['fk'] }), '9:16').blockers).toEqual([REFERENCE_MISSING])
    // 恢复首帧或连上参考图后不再拦
    expect(resolveInputs(nodes[1], nodes, [edge('k', 'v', 'first_frame')], runs({ k: ['fk'] }), '9:16').blockers).toEqual([])
  })

  it('intercepts references mixed with a first frame, with the backend wording', () => {
    const nodes = [node('a', 'image', { role: 'character' }), node('k', 'image'), node('v', 'video', { prompt: '对视' })]
    const edges = [edge('a', 'v', 'reference'), edge('k', 'v', 'first_frame')]
    const { blockers } = resolveInputs(nodes[2], nodes, edges, runs({ a: ['ra'], k: ['fk'] }), '9:16')
    expect(blockers).toEqual([REFERENCE_WITH_FRAME])
    expect(REFERENCE_WITH_FRAME).toBe('参考图和首帧不能同时使用：去掉首帧连线，或去掉参考图连线')
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
    // 角色节点带 role / character，默认出 4 张候选
    expect(nodes[0].data).toMatchObject({ role: 'character', character: '林夏', count: 4 })
    expect(nodes[2].data).toMatchObject({ duration: 4, tier: 'preview', label: '1 · 推门' })
    expect(nodes[3].data.label).toBe('2 · 镜头 2 关键帧')
    expect(Math.min(...nodes.map((item) => item.position.y))).toBeGreaterThan(0)
    // 行距放得下一个图片 / 视频节点（约 500）加镜头组的标题和内边距，镜头之间、角色之间都不重叠
    expect(nodes[3].position.y - nodes[1].position.y).toBeGreaterThanOrEqual(500 + 24 * 2 + 44)
    expect(edges.map((item) => item.targetHandle)).toEqual(['reference', 'first_frame', 'first_frame'])
    expect(edges[0]).toMatchObject({ source: nodes[0].id, target: nodes[1].id })
  })
})

describe('statusText', () => {
  const job = { id: 'j', status: 'running', progress: 0, queue_position: 0, error: '', model: 'h3', created_at: null }
  it('shows percentage while generating and queue position while waiting', () => {
    expect(statusText({ ...job, progress: 37 })).toBe('生成中 37%')
    expect(statusText(job)).toBe('生成中')
    expect(statusText({ ...job, status: 'queued', queue_position: 2 })).toBe('排队中 · 前面 2 个')
    expect(statusText({ ...job, status: 'queued' })).toBe('排队中')
    expect(statusText({ ...job, status: 'succeeded', progress: 100 })).toBe('完成')
  })
})

// 与后端 resolve_node_request 共用同一份 fixture：同一张图和结果必须得到相同的请求与 blockers
describe('resolveInputs matches the backend resolver fixture', () => {
  interface Case {
    name: string
    nodes: { id: string; type: StoredNode['type']; data: Record<string, unknown> }[]
    edges: { source: string; target: string; targetHandle: string }[]
    results: Record<string, string[]>
    aspect_ratio: string
    node_id: string
    expected: { request: unknown; blockers: string[] }
  }
  it.each(fixture.cases as unknown as Case[])('$name', (item) => {
    const nodes = item.nodes.map((entry) => node(entry.id, entry.type, entry.data))
    const edges = item.edges.map((entry) => edge(entry.source, entry.target, entry.targetHandle))
    const target = nodes.find((entry) => entry.id === item.node_id)!
    const { request, blockers } = resolveInputs(target, nodes, edges, runs(item.results), item.aspect_ratio)
    expect(JSON.parse(JSON.stringify(request))).toEqual(item.expected.request)
    expect(blockers).toEqual(item.expected.blockers)
  })
})

describe('audio nodes and voice edges', () => {
  const nodes = [node('t', 'text'), node('a', 'audio'), node('b', 'audio'), node('v', 'video'), node('i', 'image')]

  it('connects audio to video / image through voice, many at once, and text to audio through prompt', () => {
    expect(canConnect(nodes, [], { source: 'a', target: 'v', targetHandle: 'voice' })).toBe(true)
    expect(canConnect(nodes, [], { source: 'a', target: 'i', targetHandle: 'voice' })).toBe(true)
    expect(canConnect(nodes, [edge('a', 'v', 'voice')], { source: 'b', target: 'v', targetHandle: 'voice' })).toBe(true)
    expect(canConnect(nodes, [edge('a', 'v', 'voice')], { source: 'a', target: 'v', targetHandle: 'voice' })).toBe(false)
    expect(canConnect(nodes, [], { source: 't', target: 'a', targetHandle: 'prompt' })).toBe(true)
  })

  it('rejects illegal edges around audio', () => {
    expect(canConnect(nodes, [], { source: 'i', target: 'v', targetHandle: 'voice' })).toBe(false)
    expect(canConnect(nodes, [], { source: 'a', target: 'v', targetHandle: 'first_frame' })).toBe(false)
    expect(canConnect(nodes, [], { source: 'a', target: 'b', targetHandle: 'prompt' })).toBe(false)
    expect(canConnect(nodes, [], { source: 'v', target: 'a', targetHandle: 'prompt' })).toBe(false)
    expect(canConnect(nodes, [], { source: 'a', target: 'a', targetHandle: 'prompt' })).toBe(false)
  })

  it('has defaults, a label and a drop target for audio', () => {
    expect(defaultData('audio')).toEqual({ label: '', text: '', speaker: '', voice: '' })
    expect(label(node('a', 'audio'))).toBe('配音')
    expect(dropTarget('audio', 'source', 'out')).toEqual({ type: 'video', handle: 'voice', reverse: false })
    expect(dropTarget('audio', 'target', 'prompt')).toEqual({ type: 'text', handle: 'prompt', reverse: true })
    expect(dropTarget('video', 'target', 'voice')).toEqual({ type: 'audio', handle: 'voice', reverse: true })
  })

  it('resolves a tts request: own voice > speaker voice > default, linked text first', () => {
    const graph = [
      node('c', 'image', { role: 'character', character: '狐妖', voice: 'Serena' }),
      node('c2', 'image', { role: 'character', character: '狐妖', look: '冬装', voice: 'Moon' }),
      node('a', 'audio', { text: '自己的', speaker: '狐妖' }),
      node('b', 'audio', { text: '我也说', speaker: '狐妖', voice: 'Kai' }),
      node('n', 'audio', { text: '很久以前', speaker: '旁白' }),
      node('t', 'text', { text: ' 连入的台词 ' }),
    ]
    const request = (id: string, edges: StoredEdge[] = []) => resolveInputs(graph.find((item) => item.id === id)!, graph, edges, null, '9:16')
    expect(request('a').request).toEqual({ kind: 'tts', text: '自己的', voice: 'Serena' })
    expect(request('b').request.voice).toBe('Kai')
    expect(request('n').request).toEqual({ kind: 'tts', text: '很久以前', voice: '' })
    expect(request('a', [edge('t', 'a', 'prompt')]).request.text).toBe('连入的台词')
    expect(speakerVoice(graph, [], '路人')).toBe('')
  })

  it('blocks empty and over-long lines', () => {
    expect(resolveInputs(node('a', 'audio', { text: ' ' }), [node('a', 'audio')], [], null, '9:16').blockers).toEqual(['写台词，或连入一个文本节点'])
    expect(resolveInputs(node('a', 'audio', { text: '字'.repeat(501) }), [], [], null, '9:16').blockers).toEqual(['台词不能超过 500 字'])
  })
})

describe('layoutSplit lines', () => {
  it('turns shot lines into audio nodes right of the video, linked by voice edges and ordered', () => {
    const split = {
      characters: [{ name: '狐妖', description: '白发' }],
      shots: [
        { title: '开场', prompt: '雪夜', characters: ['狐妖'], duration_s: 4, lines: [{ speaker: '狐妖', text: '你来了' }, { speaker: '', text: '旁白一句' }, { speaker: '书生', text: ' ' }] },
        { title: '对视', prompt: '回头', characters: [], duration_s: 3 },
      ],
    }
    const laid = layoutSplit(split, [])
    const audios = laid.nodes.filter((item) => item.type === 'audio')
    const videos = laid.nodes.filter((item) => item.type === 'video')
    expect(audios).toHaveLength(2)  // 空台词丢弃；没写 lines 的镜头不生成
    expect(audios.map((item) => item.data)).toEqual([
      { label: '台词 · 狐妖', speaker: '狐妖', text: '你来了', voice: '', order: 0 },
      { label: '台词 · 旁白', speaker: '旁白', text: '旁白一句', voice: '', order: 1 },
    ])
    expect(audios.every((item) => item.position.x > videos[0].position.x)).toBe(true)
    expect(audios[1].position.y).toBeGreaterThan(audios[0].position.y)
    const voiceEdges = laid.edges.filter((item) => item.targetHandle === 'voice')
    expect(voiceEdges.map((item) => item.target)).toEqual([videos[0].id, videos[0].id])
    expect(voiceEdges.map((item) => item.source)).toEqual(audios.map((item) => item.id))
  })
})
