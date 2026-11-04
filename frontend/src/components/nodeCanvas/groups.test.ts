import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CanvasRuns, StoredEdge } from '../../api/nodeCanvas'
import {
  CARD_SIZE, DEFAULT_SIZE, GROUP_HEADER, GROUP_PADDING, buildGroups, collapseKey, contentBottom, expandWithRoom, groupStatus, withCachedSizes, tidyLayout, visibleRect, defaultCollapsed, focusViewport, groupBounds, loadCollapsed, nodeRect, saveCollapsed, unionRect,
  type SizedNode,
} from './groups'

const node = (id: string, type: SizedNode['type'], data: Record<string, unknown> = {}, x = 0, y = 0, measured?: SizedNode['measured']): SizedNode => ({ id, type, position: { x, y }, data, measured })
const edge = (source: string, target: string, targetHandle: string): StoredEdge => ({ id: `${source}-${target}-${targetHandle}`, source, target, sourceHandle: 'out', targetHandle })

/** 一个标准镜头：文本 → 画面 → 视频 */
function shot(n: number, y: number) {
  return {
    nodes: [node(`t${n}`, 'text', {}, 0, y), node(`k${n}`, 'image', { label: `${n} · 开场 关键帧`, shot_size: '近景' }, 400, y), node(`v${n}`, 'video', { label: `${n} · 开场`, duration: 4, order: n - 1 }, 800, y)],
    edges: [edge(`t${n}`, `k${n}`, 'prompt'), edge(`k${n}`, `v${n}`, 'first_frame')],
  }
}

describe('buildGroups membership', () => {
  it('groups main, first-frame image, video and the text that only feeds this shot', () => {
    const a = shot(1, 0)
    const groups = buildGroups(a.nodes, a.edges, null)
    expect(groups).toHaveLength(1)
    expect(groups[0]).toMatchObject({ shotId: 'v1', index: 0, shotSize: '近景', duration: 4, caption: '未生成', state: 'none' })
    expect([...groups[0].members].sort()).toEqual(['k1', 't1', 'v1'])
  })

  it('keeps character nodes out of every group, even when they feed the key frame', () => {
    const a = shot(1, 0)
    const nodes = [...a.nodes, node('c', 'image', { role: 'character', character: '狐妖' }, -400, 0)]
    const groups = buildGroups(nodes, [...a.edges, edge('c', 'k1', 'reference')], null)
    expect(groups[0].members).not.toContain('c')
  })

  it('leaves out text feeding several shots, and text nodes with no outgoing edge', () => {
    const a = shot(1, 0)
    const b = shot(2, 600)
    const shared = node('shared', 'text', {}, -400, 300)
    const lonely = node('lonely', 'text', {}, -400, 900)
    const edges = [...a.edges, ...b.edges, edge('shared', 'k1', 'prompt'), edge('shared', 'k2', 'prompt')]
    const groups = buildGroups([...a.nodes, ...b.nodes, shared, lonely], edges, null)
    expect(groups).toHaveLength(2)
    for (const group of groups) {
      expect(group.members).not.toContain('shared')
      expect(group.members).not.toContain('lonely')
    }
  })

  it('does not group a first frame shared by two videos', () => {
    const nodes = [node('k', 'image'), node('v1', 'video', { order: 0 }), node('v2', 'video', { order: 1 })]
    const groups = buildGroups(nodes, [edge('k', 'v1', 'first_frame'), edge('k', 'v2', 'first_frame')], null)
    expect(groups.map((group) => group.members)).toEqual([['v1'], ['v2']])
  })

  it('makes a frame-only shot a group of its own', () => {
    const groups = buildGroups([node('k', 'image', { prompt: '空镜', shot_size: '远景' })], [], null)
    expect(groups).toHaveLength(1)
    expect(groups[0]).toMatchObject({ shotId: 'k', duration: null, shotSize: '远景' })
    expect(groups[0].members).toEqual(['k'])
  })

  it('carries index, caption, state and thumbnail from the shot', () => {
    const a = shot(1, 0)
    const b = shot(2, 600)
    const runs: CanvasRuns = {
      nodes: {
        v1: { jobs: [{ id: 'j', status: 'running', progress: 60, queue_position: 0, error: '', model: '', created_at: null }], results: [] },
        k1: { jobs: [], results: [{ artifact_id: 'a1', type: 'image', url: '/a1' }] },
        v2: { jobs: [{ id: 'j2', status: 'failed', progress: 0, queue_position: 0, error: 'x', model: '', created_at: null }], results: [] },
      },
      active_jobs: 1, spent: 0,
    }
    const groups = buildGroups([...a.nodes, ...b.nodes], [...a.edges, ...b.edges], runs)
    expect(groups.map((group) => group.index)).toEqual([0, 1])
    expect(groups[0]).toMatchObject({ caption: '视频 60%', state: 'running' })
    expect(groups[0].thumbnail?.artifact_id).toBe('a1')
    expect(groups[1]).toMatchObject({ caption: '失败', state: 'failed' })
  })
})

describe('group bounds', () => {
  it('uses measured sizes, falls back to defaults, and adds padding and a header', () => {
    const measured = node('m', 'image', {}, 100, 200, { width: 300, height: 400 })
    expect(nodeRect(measured)).toEqual({ x: 100, y: 200, width: 300, height: 400 })
    expect(nodeRect(node('t', 'text', {}, 5, 6))).toEqual({ x: 5, y: 6, ...DEFAULT_SIZE.text })
    expect(groupBounds([{ x: 100, y: 200, width: 300, height: 400 }, { x: 500, y: 250, width: 100, height: 100 }])).toEqual({
      x: 100 - GROUP_PADDING, y: 200 - GROUP_PADDING - GROUP_HEADER,
      width: 500 + GROUP_PADDING * 2, height: 400 + GROUP_PADDING * 2 + GROUP_HEADER,
    })
  })

  it('follows member positions and measured sizes', () => {
    const a = shot(1, 0)
    const moved = a.nodes.map((item) => item.id === 'v1' ? { ...item, position: { x: 1000, y: 100 }, measured: { width: 280, height: 300 } } : item)
    const [before] = buildGroups(a.nodes, a.edges, null)
    const [after] = buildGroups(moved, a.edges, null)
    expect(after.bounds.x).toBe(before.bounds.x)
    expect(after.bounds.width).toBeGreaterThan(before.bounds.width)
    expect(after.bounds.x + after.bounds.width).toBe(1000 + 280 + GROUP_PADDING)
  })

  it('unionRect of nothing is null', () => {
    expect(unionRect([])).toBeNull()
    expect(CARD_SIZE.width).toBeGreaterThan(0)
  })
})

describe('collapse state', () => {
  afterEach(() => { window.localStorage.clear(); vi.restoreAllMocks() })

  it('collapses everything only when there are more than 4 shots', () => {
    expect([...defaultCollapsed(['a', 'b', 'c', 'd'])]).toEqual([])
    expect([...defaultCollapsed(['a', 'b', 'c', 'd', 'e'])]).toEqual(['a', 'b', 'c', 'd', 'e'])
  })

  it('stores per canvas and reads back; unknown canvas has nothing stored', () => {
    saveCollapsed('c1', new Set(['v1']))
    expect(window.localStorage.getItem(collapseKey('c1'))).toBe('["v1"]')
    expect([...(loadCollapsed('c1') || [])]).toEqual(['v1'])
    expect(loadCollapsed('c2')).toBeNull()
  })

  it('survives broken or blocked storage', () => {
    window.localStorage.setItem(collapseKey('c1'), '{oops')
    expect(loadCollapsed('c1')).toBeNull()
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(loadCollapsed('c1')).toBeNull()
    expect(() => saveCollapsed('c1', new Set(['v']))).not.toThrow()
  })
})

describe('focusViewport', () => {
  const size = { width: 1000, height: 800 }

  it('centres the area in the part of the viewport below the toolbar', () => {
    const rect = { x: 0, y: 0, width: 200, height: 100 }
    const view = focusViewport(rect, size, 100)
    expect(view.zoom).toBe(1)
    // 屏幕上区域的中心 = (x + 100*zoom, y + 50*zoom)：水平居中，垂直在工具栏下方可见区域的正中
    expect(view.x + 100).toBeCloseTo(500)
    expect(view.y + 50).toBeCloseTo(100 + (800 - 100) / 2)
  })

  it('never puts the area under the toolbar and zooms out to fit a big area', () => {
    const rect = { x: 0, y: 0, width: 3000, height: 2000 }
    const view = focusViewport(rect, size, 120)
    expect(view.zoom).toBeLessThan(0.4)
    expect(view.y).toBeGreaterThanOrEqual(120)  // 区域上沿在工具栏下沿之下
    expect(view.y + rect.height * view.zoom).toBeLessThanOrEqual(800)
  })

  it('caps zoom at maxZoom and tolerates a zero-size viewport', () => {
    expect(focusViewport({ x: 0, y: 0, width: 10, height: 10 }, size, 0).zoom).toBe(1)
    expect(Number.isFinite(focusViewport({ x: 0, y: 0, width: 10, height: 10 }, { width: 0, height: 0 }, 72).x)).toBe(true)
  })
})

describe('shared nodes stay out of groups', () => {
  it('keeps a first frame that also feeds another shot out of its own group (and its text with it)', () => {
    const a = shot(1, 0)
    const b = shot(2, 600)
    const edges = [...a.edges, ...b.edges, edge('k1', 'k2', 'reference')]
    const groups = buildGroups([...a.nodes, ...b.nodes], edges, null)
    expect(groups[0].members).toEqual(['v1'])  // k1 连到镜头二，t1 只喂 k1 → 一并不归组
    expect(groups[1].members).toContain('k2')
  })
})

describe('tidyLayout', () => {
  it('puts characters left, shots top to bottom with text, image, video left to right, others right', () => {
    const a = shot(1, 0)
    const b = shot(2, 100)
    const c = node('c', 'image', { role: 'character', character: '狐妖' }, 50, 50)
    const loose = node('loose', 'text', {}, 7, 7)
    const nodes = [...b.nodes, ...a.nodes, c, loose]  // 故意乱序
    const pos = tidyLayout(nodes, [...a.edges, ...b.edges, edge('c', 'k1', 'reference')], null)
    expect(pos.c.x).toBeLessThan(pos.t1.x)
    expect(pos.t1.x).toBeLessThan(pos.k1.x)
    expect(pos.k1.x).toBeLessThan(pos.v1.x)
    expect(pos.v1.x).toBeLessThan(pos.loose.x)
    // 组 2 在组 1 下方，且组框互不重叠
    const groups = buildGroups(nodes.map((item) => ({ ...item, position: pos[item.id] })), [...a.edges, ...b.edges, edge('c', 'k1', 'reference')], null)
    const [g1, g2] = groups
    expect(g2.bounds.y).toBeGreaterThanOrEqual(g1.bounds.y + g1.bounds.height)
  })

  it('uses measured heights so tall nodes do not overlap the next group', () => {
    const a = shot(1, 0)
    const b = shot(2, 0)
    const nodes = [...a.nodes.map((item) => item.id === 'k1' ? { ...item, measured: { width: 280, height: 900 } } : item), ...b.nodes]
    const pos = tidyLayout(nodes, [...a.edges, ...b.edges], null)
    expect(pos.k2.y).toBeGreaterThanOrEqual(pos.k1.y + 900 + GROUP_PADDING + GROUP_HEADER)
  })

  it('contentBottom is the lowest node or group edge', () => {
    const a = shot(1, 0)
    const groups = buildGroups(a.nodes, a.edges, null)
    expect(contentBottom([], [])).toBeNull()
    expect(contentBottom(a.nodes, groups)).toBe(groups[0].bounds.y + groups[0].bounds.height)
  })
})

describe('groupStatus', () => {
  const st = (state: 'none' | 'running' | 'failed' | 'done', progress = 0, queued = false) => ({ state, progress, queued })
  it('says only the progress, never the shot size', () => {
    expect(groupStatus(st('none'), st('none'), true)).toBe('未生成')
    expect(groupStatus(st('running', 40), st('none'), true)).toBe('画面生成中 40%')
    expect(groupStatus(st('running', 0, true), st('none'), true)).toBe('画面排队中')
    expect(groupStatus(st('running'), st('none'), true)).toBe('画面生成中')
    expect(groupStatus(st('done'), st('none'), true)).toBe('图片已选，待转视频')
    expect(groupStatus(st('done'), st('none'), false)).toBe('画面已完成')
    expect(groupStatus(st('done'), st('running', 60), true)).toBe('视频生成中 60%')
    expect(groupStatus(st('done'), st('running', 0, true), true)).toBe('视频排队中')
    expect(groupStatus(st('done'), st('done'), true)).toBe('视频已完成')
    expect(groupStatus(st('failed'), st('none'), true)).toBe('画面失败')
    expect(groupStatus(st('done'), st('failed'), true)).toBe('视频失败')
  })

  it('is carried on the group', () => {
    const a = shot(1, 0)
    expect(buildGroups(a.nodes, a.edges, null)[0].status).toBe('未生成')
  })
})

describe('compact layout and making room', () => {
  const four = () => {
    const parts = [1, 2, 3].map((n) => shot(n, 0))
    return { nodes: parts.flatMap((part) => part.nodes), edges: parts.flatMap((part) => part.edges) }
  }
  const place = (nodes: SizedNode[], pos: Record<string, { x: number; y: number }>) => nodes.map((item) => ({ ...item, position: pos[item.id] || item.position }))
  const overlaps = (a: { x: number; y: number; width: number; height: number }, b: typeof a) =>
    a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
  const visible = (nodes: SizedNode[], edges: StoredEdge[], collapsed: Set<string>) =>
    buildGroups(nodes, edges, null).map((group) => visibleRect(group, collapsed.has(group.shotId)))

  it('tidy puts folded groups close together, only card-high apart', () => {
    const { nodes, edges } = four()
    const all = new Set(['v1', 'v2', 'v3'])
    const compact = tidyLayout(nodes, edges, null, all)
    const rects = visible(place(nodes, compact), edges, all)
    expect(rects[1].y - rects[0].y).toBeLessThan(150)
    expect(rects[2].y - rects[1].y).toBeLessThan(150)
    for (let i = 0; i < 3; i++) for (let j = i + 1; j < 3; j++) expect(overlaps(rects[i], rects[j])).toBe(false)
    // 展开时按实际高度排
    const open = tidyLayout(nodes, edges, null, new Set())
    expect(open.k2.y - open.k1.y).toBeGreaterThan(500)
  })

  it('pushes the groups below down when one unfolds, to its bottom plus the gap', () => {
    const { nodes, edges } = four()
    const all = new Set(['v1', 'v2', 'v3'])
    const compactNodes = place(nodes, tidyLayout(nodes, edges, null, all))
    const { positions, collapsed } = expandWithRoom(compactNodes, edges, null, all, ['v1'])
    expect([...collapsed].sort()).toEqual(['v2', 'v3'])
    const after = place(compactNodes, positions)
    const rects = visible(after, edges, collapsed)
    expect(rects[1].y).toBeGreaterThanOrEqual(rects[0].y + rects[0].height + 48)
    expect(rects[1].y).toBeLessThan(rects[0].y + rects[0].height + 48 + 1)  // 刚好到间距为止，不多推
    expect(overlaps(rects[0], rects[1])).toBe(false)
    expect(positions.k1).toBeUndefined()  // 展开的组自己不动
  })

  it('moves nothing when there is already enough room', () => {
    const { nodes, edges } = four()
    const spread = place(nodes, tidyLayout(nodes, edges, null, new Set()))
    expect(expandWithRoom(spread, edges, null, new Set(['v1']), ['v1']).positions).toEqual({})
  })

  it('expanding every group in order leaves none overlapping', () => {
    const { nodes, edges } = four()
    const all = new Set(['v1', 'v2', 'v3'])
    const compactNodes = place(nodes, tidyLayout(nodes, edges, null, all))
    const { positions, collapsed } = expandWithRoom(compactNodes, edges, null, all, ['v1', 'v2', 'v3'])
    expect(collapsed.size).toBe(0)
    const rects = visible(place(compactNodes, positions), edges, collapsed)
    for (let i = 0; i < 3; i++) for (let j = i + 1; j < 3; j++) expect(overlaps(rects[i], rects[j])).toBe(false)
  })

  it('contentBottom counts folded groups as cards', () => {
    const { nodes, edges } = four()
    const all = new Set(['v1', 'v2', 'v3'])
    const compactNodes = place(nodes, tidyLayout(nodes, edges, null, all))
    const groups = buildGroups(compactNodes, edges, null)
    const bottom = contentBottom(compactNodes, groups, all) as number
    expect(bottom).toBeLessThan(groups[2].bounds.y + 200)
  })
})

describe('size cache', () => {
  it('prefers a fresh measurement, then the cached size, then the default', () => {
    const cache = new Map([['a', { width: 300, height: 700 }], ['b', { width: 300, height: 700 }]])
    const [a, b, c] = withCachedSizes([node('a', 'image'), node('b', 'image', {}, 0, 0, { width: 280, height: 400 }), node('c', 'image')], cache)
    expect(nodeRect(a).height).toBe(700)  // 没测量：用缓存
    expect(nodeRect(b).height).toBe(400)  // 有测量：用测量
    expect(nodeRect(c).height).toBe(DEFAULT_SIZE.image.height)  // 都没有：默认
  })

  it('makes room with the cached (taller) size instead of the default', () => {
    const parts = [1, 2].map((n) => shot(n, 0))
    const nodes = parts.flatMap((part) => part.nodes)
    const edges = parts.flatMap((part) => part.edges)
    const all = new Set(['v1', 'v2'])
    const placed = nodes.map((item, i) => ({ ...item, position: { x: item.position.x, y: i < 3 ? 68 : 68 + 100 } }))
    const cache = new Map(nodes.map((item) => [item.id, { width: 280, height: 900 }]))
    const plain = expandWithRoom(placed, edges, null, all, ['v1']).positions
    const cached = expandWithRoom(withCachedSizes(placed, cache), edges, null, all, ['v1']).positions
    expect(cached.k2.y).toBeGreaterThan(plain.k2.y)
  })
})

describe('expandWithRoom collision chain', () => {
  it('pushes only what collides, one after another, and leaves far-away nodes alone', () => {
    const [a, b, c] = [shot(1, 68), shot(2, 500), shot(3, 560)]
    const far = node('far', 'text', {}, 0, 10000)
    const nodes = [...a.nodes, ...b.nodes, ...c.nodes, far]
    const edges = [...a.edges, ...b.edges, ...c.edges]
    const { positions } = expandWithRoom(nodes, edges, null, new Set(['v2', 'v3']), ['v1'])
    const bottom1 = 68 + DEFAULT_SIZE.video.height + GROUP_PADDING  // 镜头一展开后的组框底边
    const card2Top = 500 - GROUP_PADDING - GROUP_HEADER
    expect(positions.k2.y).toBe(500 + (bottom1 + 48 - card2Top))  // 第一个推到底边 + 48
    // 第二个再让到第一个被推后的卡片底边 + 48
    expect(positions.k3.y - GROUP_PADDING - GROUP_HEADER).toBe(card2Top + (bottom1 + 48 - card2Top) + CARD_SIZE.height + 48)
    expect(positions.far).toBeUndefined()
  })

  it('does not move anything that already has enough room, even if it is below', () => {
    const [a, b] = [shot(1, 68), shot(2, 3000)]
    const { positions } = expandWithRoom([...a.nodes, ...b.nodes], [...a.edges, ...b.edges], null, new Set(['v2']), ['v1'])
    expect(positions).toEqual({})
  })
})

describe('audio nodes in groups', () => {
  it('puts a line that only voices this shot into its group, and leaves out lines shared by two shots', () => {
    const a = shot(1, 0)
    const b = shot(2, 600)
    const own = node('own', 'audio', { speaker: '狐妖', text: '你来了' }, 1200, 0)
    const shared = node('shared', 'audio', { speaker: '旁白', text: '那一夜' }, 1200, 300)
    const edges = [...a.edges, ...b.edges, edge('own', 'v1', 'voice'), edge('shared', 'v1', 'voice'), edge('shared', 'v2', 'voice')]
    const groups = buildGroups([...a.nodes, ...b.nodes, own, shared], edges, null)
    expect(groups[0].members).toContain('own')
    expect(groups[1].members).not.toContain('own')
    for (const group of groups) expect(group.members).not.toContain('shared')
  })

  it('groups lines of an image-only shot through the image node, and ignores audio without edges', () => {
    const nodes = [node('k', 'image', { label: '1 · 远景' }), node('l', 'audio'), node('free', 'audio')]
    const groups = buildGroups(nodes, [edge('l', 'k', 'voice')], null)
    expect(groups).toHaveLength(1)
    expect([...groups[0].members].sort()).toEqual(['k', 'l'])
  })

  it('does not count audio nodes as shots and lays them out right of the video', () => {
    const a = shot(1, 0)
    const line = node('line', 'audio', { speaker: '狐妖', text: '你来了' }, 1200, 0)
    const edges = [...a.edges, edge('line', 'v1', 'voice')]
    const nodes = [...a.nodes, line]
    expect(buildGroups(nodes, edges, null)).toHaveLength(1)
    const laid = tidyLayout(nodes, edges, null)
    expect(laid.line.x).toBeGreaterThan(laid.v1.x)
    expect(DEFAULT_SIZE.audio.width).toBe(280)
  })
})
