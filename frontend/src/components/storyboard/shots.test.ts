import { describe, expect, it } from 'vitest'
import type { CanvasRuns, NodeJob, StoredEdge, StoredNode } from '../../api/nodeCanvas'
import { appendPhrase, buildEditChain, buildShots, editRequest, newShot, priceSuffix, reorderPatch, shotTitle, totalSeconds } from './shots'

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
