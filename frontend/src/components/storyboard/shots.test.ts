import { describe, expect, it } from 'vitest'
import { buildCharacters } from '../characters/characters'
import type { CanvasRuns, NodeJob, StoredEdge, StoredNode } from '../../api/nodeCanvas'
import { appendPhrase, applyVideoMode, buildEditChain, buildShots, clampDuration, costLabel, durationOptions, durationRange, editRequest, newShot, priceSuffix, reorderPatch, shotCharacters, shotReferenceUse, shotTitle, totalSeconds, videoMode } from './shots'

const node = (id: string, type: StoredNode['type'], data: Record<string, unknown> = {}, x = 0, y = 0): StoredNode => ({ id, type, position: { x, y }, data })
const edge = (source: string, target: string, targetHandle: string): StoredEdge => ({ id: `${source}-${target}`, source, target, sourceHandle: 'out', targetHandle })
const job = (status: string, progress = 0): NodeJob => ({ id: 'j', status, progress, queue_position: 0, error: '', model: '', created_at: null })
const runs = (entries: Record<string, { results?: string[]; jobs?: NodeJob[] }>): CanvasRuns => ({
  nodes: Object.fromEntries(Object.entries(entries).map(([id, value]) => [id, {
    jobs: value.jobs || [], results: (value.results || []).map((artifact_id) => ({ artifact_id, type: 'image', url: `/api/artifacts/${artifact_id}/download` })),
  }])),
  active_jobs: 0,
  spent: 0,
})

describe('buildShots: which nodes are shots', () => {
  it('makes a shot per video node with its first-frame image, and skips character images', () => {
    const nodes = [node('char', 'image', { label: '角色 · 狐妖' }), node('k1', 'image', { prompt: '开场' }), node('v1', 'video', { label: '1 · 开场' })]
    const edges = [edge('char', 'k1', 'reference'), edge('k1', 'v1', 'first_frame')]
    const shots = buildShots(nodes, edges, null)
    expect(shots.map((shot) => shot.id)).toEqual(['v1'])
    expect(shots[0]).toMatchObject({ title: '开场', description: '开场', index: 0 })
    expect(shots[0].frame?.id).toBe('k1')
    expect(shots[0].video?.id).toBe('v1')
  })

  it('never counts a role=character image as a shot, even when nothing references it yet', () => {
    const nodes = [node('lonely', 'image', { role: 'character', character: '狐妖' }), node('k', 'image', { prompt: '空镜' })]
    expect(buildShots(nodes, [], null).map((shot) => shot.id)).toEqual(['k'])
  })

  it('counts an image with no video and no outgoing reference as a picture-only shot', () => {
    const nodes = [node('char', 'image'), node('user', 'image', { label: '2 · 空镜 关键帧', prompt: '空镜' }), node('k', 'image'), node('v', 'video')]
    const edges = [edge('char', 'user', 'reference'), edge('k', 'v', 'first_frame')]
    // char 只通过 reference 连出去 = 角色图；user 没有出边 = 只有画面的镜头；k 已是视频的首帧，不单独算
    expect(buildShots(nodes, edges, null).map((shot) => shot.id).sort()).toEqual(['user', 'v'])
    const only = buildShots([node('solo', 'image', { label: '3 · 空镜 关键帧', prompt: '空镜' })], [], null)[0]
    expect(only).toMatchObject({ id: 'solo', title: '空镜', description: '空镜', video: undefined })
    expect(only.frame?.id).toBe('solo')
  })

  it('ignores text nodes', () => {
    expect(buildShots([node('t', 'text', { text: '想法' })], [], null)).toEqual([])
  })
})

describe('buildShots: character reference edges into video', () => {
  const base = () => [
    node('char', 'image', { role: 'character', character: '狐妖' }), node('legacy', 'image', { label: '角色 · 书生' }),
    node('k1', 'image', { prompt: '开场' }), node('v1', 'video', { label: '1 · 开场' }),
  ]

  it('does not turn characters into shots, or the video shot into a character, when characters feed the video', () => {
    const edges = [edge('char', 'v1', 'reference'), edge('legacy', 'v1', 'reference'), edge('k1', 'v1', 'first_frame')]
    const shots = buildShots(base(), edges, null)
    expect(shots.map((shot) => shot.id)).toEqual(['v1'])
    expect(shots[0].frame?.id).toBe('k1')
  })

  it('keeps the unlinked first-frame image in its shot when the video records it as parked', () => {
    const nodes = base()
    nodes[3].data.parked_first_frame = 'k1'
    const edges = [edge('char', 'v1', 'reference'), edge('legacy', 'v1', 'reference')]
    const shots = buildShots(nodes, edges, null)
    expect(shots.map((shot) => shot.id)).toEqual(['v1'])
    expect(shots[0].frame?.id).toBe('k1')
    // 没有记录时，被摘掉连线的图片会成为独立的“只有画面”镜头
    expect(buildShots(base(), edges, null).map((shot) => shot.id).sort()).toEqual(['k1', 'v1'])
  })
})

describe('video generation mode', () => {
  const graph = () => ({
    nodes: [
      node('char', 'image', { role: 'character', character: '狐妖' }), node('boy', 'image', { role: 'character', character: '书生' }),
      node('k1', 'image', { prompt: '雪夜竹林' }), node('v1', 'video', { label: '1 · 开场' }),
    ],
    edges: [edge('char', 'k1', 'reference'), edge('boy', 'k1', 'reference'), edge('k1', 'v1', 'first_frame')],
  })

  it('finds the characters on the shot through the key frame reference edges', () => {
    const { nodes, edges } = graph()
    const [shot] = buildShots(nodes, edges, null)
    expect(shotCharacters(shot, nodes, edges).map((item) => item.name)).toEqual(['狐妖', '书生'])
    expect(videoMode(shot, edges)).toBe('frame')
    const lone = buildShots([node('k', 'image'), node('v', 'video')], [edge('k', 'v', 'first_frame')], null)[0]
    expect(shotCharacters(lone, [node('k', 'image'), node('v', 'video')], [edge('k', 'v', 'first_frame')])).toEqual([])
  })

  it('switches to reference: base looks feed the video, first frame edge goes, the image stays and is parked', () => {
    const { nodes, edges } = graph()
    const [shot] = buildShots(nodes, edges, null)
    const next = applyVideoMode(shot, 'reference', nodes, edges)
    const intoVideo = next.edges.filter((item) => item.target === 'v1')
    expect(intoVideo.map((item) => [item.source, item.targetHandle])).toEqual([['char', 'reference'], ['boy', 'reference']])
    expect(next.nodes.map((item) => item.id)).toEqual(nodes.map((item) => item.id))
    expect(next.nodes.find((item) => item.id === 'v1')!.data).toMatchObject({ parked_first_frame: 'k1', prompt: '雪夜竹林' })
    // 角色到关键帧的出镜连线保持不变，镜头仍是同一个，且处于参考图模式
    expect(next.edges.filter((item) => item.target === 'k1')).toHaveLength(2)
    const [after] = buildShots(next.nodes, next.edges, null)
    expect(after.id).toBe('v1')
    expect(after.frame?.id).toBe('k1')
    expect(videoMode(after, next.edges)).toBe('reference')
    expect(shotCharacters(after, next.nodes, next.edges)).toHaveLength(2)
  })

  it('switches back: reference edges removed, first frame restored, parked marks cleared', () => {
    const { nodes, edges } = graph()
    const [shot] = buildShots(nodes, edges, null)
    const reference = applyVideoMode(shot, 'reference', nodes, edges)
    const [refShot] = buildShots(reference.nodes, reference.edges, null)
    const back = applyVideoMode(refShot, 'frame', reference.nodes, reference.edges)
    expect(back.edges.filter((item) => item.target === 'v1').map((item) => [item.source, item.targetHandle])).toEqual([['k1', 'first_frame']])
    expect(back.nodes.find((item) => item.id === 'v1')!.data).not.toHaveProperty('parked_first_frame')
    expect(videoMode(buildShots(back.nodes, back.edges, null)[0], back.edges)).toBe('frame')
  })

  it('does not overwrite an existing video prompt, and leaves other videos alone', () => {
    const { nodes, edges } = graph()
    nodes[3].data.prompt = '回头'
    const other = [...nodes, node('v2', 'video', {})]
    const shot = buildShots(other, edges, null).find((item) => item.id === 'v1')!
    const next = applyVideoMode(shot, 'reference', other, edges)
    expect(next.nodes.find((item) => item.id === 'v1')!.data.prompt).toBe('回头')
    expect(next.nodes.find((item) => item.id === 'v2')).toBe(other.find((item) => item.id === 'v2'))
  })
})

describe('video generation mode: edge cases', () => {
  const graph = (count = 2) => {
    const chars = Array.from({ length: count }, (_, index) => node(`c${index}`, 'image', { role: 'character', character: `角色${index}` }))
    return {
      nodes: [...chars, node('k1', 'image', { prompt: '雪夜竹林' }), node('v1', 'video', { label: '1 · 开场' })],
      edges: [...chars.map((item) => edge(item.id, 'k1', 'reference')), edge('k1', 'v1', 'first_frame')],
    }
  }
  const toReference = (g: ReturnType<typeof graph>) => applyVideoMode(buildShots(g.nodes, g.edges, null)[0], 'reference', g.nodes, g.edges)

  it('stays in reference mode after every reference edge is deleted, and can restore the first frame', () => {
    const switched = toReference(graph())
    const edges = switched.edges.filter((item) => item.targetHandle !== 'reference' || item.target !== 'v1')
    const [shot] = buildShots(switched.nodes, edges, null)
    expect(videoMode(shot, edges)).toBe('reference')
    expect(shotReferenceUse(shot, switched.nodes, edges).used).toBe(0)
    const back = applyVideoMode(shot, 'frame', switched.nodes, edges)
    expect(back.edges.filter((item) => item.target === 'v1').map((item) => [item.source, item.targetHandle])).toEqual([['k1', 'first_frame']])
    expect(back.nodes.find((item) => item.id === 'v1')!.data).not.toHaveProperty('parked_first_frame')
    expect(videoMode(buildShots(back.nodes, back.edges, null)[0], back.edges)).toBe('frame')
  })

  it('reports the real number and the unused characters beyond nine', () => {
    const g = graph(11)
    const before = shotReferenceUse(buildShots(g.nodes, g.edges, null)[0], g.nodes, g.edges)
    expect(before.characters).toHaveLength(11)
    expect(before.unused).toEqual(['角色9', '角色10'])
    const switched = toReference(g)
    const [shot] = buildShots(switched.nodes, switched.edges, null)
    const use = shotReferenceUse(shot, switched.nodes, switched.edges)
    expect(switched.edges.filter((item) => item.target === 'v1' && item.targetHandle === 'reference')).toHaveLength(9)
    expect(use).toMatchObject({ used: 9, unused: ['角色9', '角色10'] })
  })

  it('copies the description from an upstream text node when the key frame has no prompt of its own', () => {
    const g = graph()
    g.nodes.push(node('t', 'text', { text: '雨夜便利店门口' }))
    g.edges.push(edge('t', 'k1', 'prompt'))
    g.nodes.find((item) => item.id === 'k1')!.data.prompt = ''
    const next = toReference(g)
    expect(next.nodes.find((item) => item.id === 'v1')!.data.prompt).toBe('雨夜便利店门口')
  })

  it('does not treat a parked first frame that is also another shot reference as a character', () => {
    // A 是镜头 X 的停放首帧，同时是镜头 Y 的参考图
    const nodes = [node('A', 'image', { prompt: '共用画面' }), node('X', 'video', { parked_first_frame: 'A', order: 0 }), node('Y', 'video', { order: 1 })]
    const edges = [edge('A', 'Y', 'reference')]
    expect(buildCharacters(nodes, edges, null)).toEqual([])
    const shots = buildShots(nodes, edges, null)
    expect(shots.map((shot) => shot.id)).toEqual(['X', 'Y'])
    expect(shots[0].frame?.id).toBe('A')
    // 没有停放记录时仍按旧口径：出边全是 reference 的图片是角色
    expect(buildCharacters([node('A', 'image'), node('Y', 'video')], edges, null).map((item) => item.name)).toHaveLength(1)
  })
})

describe('buildShots: ordering', () => {
  it('sorts by data.order first, then unordered nodes by y then x', () => {
    const nodes = [
      node('c', 'video', {}, 0, 100), node('b', 'video', { order: 1 }, 0, 500), node('a', 'video', { order: 0 }, 0, 900),
      node('d', 'video', {}, 50, 100), node('e', 'video', {}, 0, 50),
    ]
    expect(buildShots(nodes, [], null).map((shot) => shot.id)).toEqual(['a', 'b', 'e', 'c', 'd'])
  })

  it('reorderPatch rewrites every shot with 0..n-1', () => {
    const shots = buildShots([node('a', 'video', {}, 0, 0), node('b', 'video', {}, 0, 10), node('c', 'video', {}, 0, 20)], [], null)
    expect(reorderPatch(shots, 0, 2)).toEqual({ b: 0, c: 1, a: 2 })
    expect(reorderPatch(shots, 2, 0)).toEqual({ c: 0, a: 1, b: 2 })
    expect(reorderPatch(shots, 0, 9)).toEqual({})
  })
})

describe('buildShots: status and thumbnail', () => {
  const nodes = [node('k', 'image', { shot_size: '近景', selected: 'old' }), node('v', 'video', { duration: 4, camera: '拉远' }), node('k2', 'image'), node('v2', 'video'), node('k3', 'image'), node('v3', 'video')]
  const edges = [edge('k', 'v', 'first_frame'), edge('k2', 'v2', 'first_frame'), edge('k3', 'v3', 'first_frame')]

  it('derives fields, caption and the selected thumbnail', () => {
    const [shot] = buildShots(nodes.slice(0, 2), edges.slice(0, 1), runs({ k: { results: ['new', 'old'] } }))
    expect(shot).toMatchObject({ shotSize: '近景', camera: '拉远', duration: 4, state: 'done', caption: '近景' })
    expect(shot.thumbnail?.artifact_id).toBe('old')
  })

  it('shows 未生成, generating progress and failure', () => {
    const shots = buildShots(nodes, edges, runs({
      v: { jobs: [job('running', 60)] }, k: { results: ['x'] },
      k2: { jobs: [job('failed')] },
    }))
    const byId = Object.fromEntries(shots.map((shot) => [shot.id, shot]))
    expect(byId.v).toMatchObject({ state: 'running', caption: '视频 60%' })
    expect(byId.v2).toMatchObject({ state: 'failed', caption: '失败' })
    expect(byId.v3).toMatchObject({ state: 'none', caption: '未生成', duration: 5 })
  })

  it('reports image progress and queueing', () => {
    const shots = buildShots(nodes.slice(0, 4), edges.slice(0, 2), runs({ k: { jobs: [job('running', 30)] }, k2: { jobs: [job('queued')] } }))
    expect(shots.map((shot) => shot.caption)).toEqual(['画面 30%', '画面排队中'])
  })

  it('sums video durations for the header', () => {
    expect(totalSeconds(buildShots(nodes, edges, null))).toBe(4 + 5 + 5)
  })
})

describe('video-only shots', () => {
  it('follows the video status when there is no frame node', () => {
    const v = [node('v', 'video', { camera: '拉远' })]
    expect(buildShots(v, [], runs({}))[0]).toMatchObject({ state: 'none', caption: '未生成' })
    expect(buildShots(v, [], runs({ v: { results: ['x'] } }))[0]).toMatchObject({ state: 'done', caption: '完成' })
  })
})

describe('shotTitle', () => {
  it('strips the numeric prefix and the 关键帧 suffix of image shots', () => {
    expect(shotTitle(node('v', 'video', { label: '12 · 推门' }), 'x')).toBe('推门')
    expect(shotTitle(node('i', 'image', { label: '3、雪夜 关键帧' }), 'x')).toBe('雪夜')
    expect(shotTitle(node('i', 'image', { label: '' }), '镜头 4')).toBe('镜头 4')
  })
})

describe('newShot', () => {
  it('adds a linked image + video below existing nodes with the next order, and backfills orders', () => {
    const nodes = [node('k', 'image', {}, 420, 0), node('v', 'video', {}, 840, 0), node('k2', 'image', {}, 420, 380), node('v2', 'video', {}, 840, 380)]
    const edges = [edge('k', 'v', 'first_frame'), edge('k2', 'v2', 'first_frame')]
    const shots = buildShots(nodes, edges, null)
    const created = newShot(nodes, shots)
    const [image, video] = created.nodes
    expect(image.type).toBe('image')
    expect(video).toMatchObject({ type: 'video', data: { order: 2 } })
    expect(image.position).toEqual({ x: 420, y: 760 })
    expect(video.position).toEqual({ x: 840, y: 760 })
    expect(created.edges).toMatchObject([{ source: image.id, target: video.id, targetHandle: 'first_frame' }])
    expect(created.orders).toEqual({ v: 0, v2: 1 })
    expect(created.mainId).toBe(video.id)
    const all = buildShots([...nodes, ...created.nodes], [...edges, ...created.edges], null)
    expect(all).toHaveLength(3)
  })
})

describe('buildEditChain', () => {
  const result = (artifact_id: string, edit_of?: string) => ({ artifact_id, type: 'image', url: '', edit_of: edit_of ?? null })
  it('labels originals 第 n 张 and edits 改 n, in time order', () => {
    // runs 里最新在前
    const chain = buildEditChain([result('e2', 'e1'), result('e1', 'o1'), result('o2'), result('o1')])
    expect(chain.map((item) => item.label)).toEqual(['第 1 张', '第 2 张', '改 1', '改 2'])
    expect(chain.map((item) => item.basedOn)).toEqual([undefined, undefined, '第 1 张', '改 1'])
    expect(chain.map((item) => item.result.artifact_id)).toEqual(['o1', 'o2', 'e1', 'e2'])
  })
})

describe('edit request helpers', () => {
  it('builds the edit request with the keep-composition suffix only in the prompt', () => {
    expect(editRequest({ base: 'a1', text: ' 表情冷一点 ', keep: true, count: 2, model: 'm', aspectRatio: '9:16' })).toEqual({
      kind: 'image', prompt: '表情冷一点，保持构图和角色长相不变', model: 'm', aspect_ratio: '9:16', count: 2, edit_of: 'a1', edit_instruction: '表情冷一点',
    })
    expect(editRequest({ base: 'a1', text: '换光线', keep: false, count: 1, model: '', aspectRatio: '9:16' }).prompt).toBe('换光线')
  })

  it('appends quick phrases', () => {
    expect(appendPhrase('', '换表情')).toBe('换表情')
    expect(appendPhrase('嘴角不要笑 ', '换光线')).toBe('嘴角不要笑，换光线')
  })
})

describe('priceSuffix', () => {
  it('formats price, missing price and no quote the same everywhere', () => {
    const quote = (estimated_cost: number | null) => ({ kind: 'image' as const, model: 'm', estimated_cost })
    expect(priceSuffix(quote(0.12))).toBe(' · ¥0.120')
    expect(priceSuffix(quote(2))).toBe(' · ¥2.00')
    expect(priceSuffix(quote(null))).toBe(' · 未配置价格')
    expect(priceSuffix(null)).toBe('')
  })
})

describe('costLabel', () => {
  it('formats actual and estimated spend and hides zero', () => {
    expect(costLabel({ actual: 0.21, estimated: 0, total: 0.21, is_estimate: false })).toBe('已花 ¥0.210')
    expect(costLabel({ actual: 0, estimated: 2, total: 2, is_estimate: true })).toBe('已花约 ¥2.00')
    expect(costLabel({ actual: 0, estimated: 0, total: 0, is_estimate: false })).toBe('')
    expect(costLabel(undefined)).toBe('')
    expect(costLabel({ actual: 1, estimated: 0, total: null, is_estimate: false })).toBe('')
  })
})

describe('durationRange', () => {
  const video = node('v', 'video', { duration: 3 })
  const model = (slug: string, modes: string[], min?: number, max?: number) => ({ slug, name: slug, model: slug, provider: 'p', modes, pricing: {}, min_duration: min, max_duration: max })
  const models = (items: ReturnType<typeof model>[], route = 'a') => ({ image: [], video: items, defaults: { image: '', video_preview: route, video_final: route } })

  it('keeps the usual 2-12 range for first-frame mode', () => {
    expect(durationRange('frame', video, null)).toEqual({ min: 2, max: 12 })
  })
  it('defaults reference mode to 4-15 when no model declares a range', () => {
    expect(durationRange('reference', video, null)).toEqual({ min: 4, max: 15 })
    expect(durationRange('reference', video, models([model('a', ['reference'])]))).toEqual({ min: 4, max: 15 })
  })
  it('uses the routed reference-capable deployment, else conservative intersection', () => {
    const items = [model('a', ['reference'], 5, 10), model('b', ['reference'], 4, 15), model('c', ['frame'], 2, 6)]
    expect(durationRange('reference', video, models(items))).toEqual({ min: 5, max: 10 })
    expect(durationRange('reference', video, models(items, 'c'))).toEqual({ min: 5, max: 10 })
  })
  it('only uses primary and fallback of the current tier, excluding unrouted deployments', () => {
    const items = [model('a', ['frame'], 2, 12), model('b', ['reference'], 5, 10), model('c', ['reference'], 4, 15)]
    const catalog = { ...models(items), video_routes: { preview: ['a', 'b'], final: ['c'] } }
    expect(durationRange('reference', video, catalog)).toEqual({ min: 5, max: 10 })
    expect(durationRange('reference', node('v', 'video', { model: 'c' }), catalog)).toEqual({ min: 5, max: 10 })
    expect(durationRange('reference', node('v', 'video', { tier: 'final' }), catalog)).toEqual({ min: 4, max: 15 })
  })
  it('defaults each missing duration boundary independently', () => {
    expect(durationRange('reference', video, models([model('a', ['reference'], 6)]))).toEqual({ min: 6, max: 15 })
    expect(durationRange('reference', video, models([model('a', ['reference'], undefined, 9)]))).toEqual({ min: 4, max: 9 })
  })
  it('lists and clamps whole seconds', () => {
    expect(durationOptions({ min: 4, max: 7 })).toEqual([4, 5, 6, 7])
    expect(clampDuration(3, { min: 4, max: 15 })).toBe(4)
    expect(clampDuration(20, { min: 4, max: 15 })).toBe(15)
    expect(clampDuration(8, { min: 4, max: 15 })).toBe(8)
  })
})
