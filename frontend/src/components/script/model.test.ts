import { describe, expect, it } from 'vitest'
import type { ScriptPayload, ScriptScene } from '../../api/script'
import {
  actTitle, addAct, ensureBlockIds, SPLIT_IDEA_LIMIT, addScene, blankScript, canMoveScene, flattenScript, hasContent, insertBlock, longDialogueHint, moveBlock, moveScene,
  newSceneId, removeScene, replaceSelection, sceneSeconds, splitShotCount, totalSeconds, wordCount,
} from './model'

const scene = (id: string, blocks: ScriptScene['blocks'] = [], title = '', heading = ''): ScriptScene => ({ id, title, heading, blocks })
let seq = 0
const action = (text: string) => ({ id: `b${++seq}`, type: 'action' as const, character: '', text })
const say = (character: string, text: string) => ({ id: `b${++seq}`, type: 'dialogue' as const, character, text })

const sample = (): ScriptPayload => ({
  acts: [
    { title: '第一幕 · 相遇', scenes: [scene('s1', [action('雪落竹林')], '雪夜竹林', '外景 · 夜'), scene('s2', [say('狐妖', '你来了。')], '破庙')] },
    { title: '第二幕 · 真相', scenes: [scene('s3', [action('灯下')])] },
  ],
})

describe('script model', () => {
  it('estimates seconds: 2s per 30 action chars and 1s per 4 dialogue chars, rounded', () => {
    expect(sceneSeconds(scene('a', [action('字'.repeat(30))]))).toBe(2)
    expect(sceneSeconds(scene('a', [say('甲', '字'.repeat(8))]))).toBe(2)
    expect(sceneSeconds(scene('a', [action('字'.repeat(30)), say('甲', '字'.repeat(8))]))).toBe(4)
    expect(sceneSeconds(scene('a', [action(' ')]))).toBe(0)
    expect(totalSeconds({ acts: [{ title: '', scenes: [scene('a', [action('字'.repeat(15))]), scene('b', [say('甲', '字'.repeat(4))])] }] })).toBe(2)
  })

  it('counts words without whitespace and detects content', () => {
    expect(wordCount(sample())).toBe(4 + 4 + 2)
    expect(hasContent(sample())).toBe(true)
    expect(hasContent(blankScript())).toBe(false)
    expect(hasContent({ acts: [] })).toBe(false)
  })

  it('hints about the first scene with long dialogue and counts the rest', () => {
    const long = '字'.repeat(31)
    expect(longDialogueHint(sample())).toBe('')
    const payload: ScriptPayload = { acts: [{ title: '', scenes: [scene('a', [action('x')]), scene('b', [say('甲', long), say('乙', long), say('丙', '短')]), scene('c', [say('甲', long)])] }] }
    expect(longDialogueHint(payload)).toBe('场 2 有 2 句对白超过 30 字，配音会偏长（另有 1 场）')
    expect(longDialogueHint({ acts: [{ title: '', scenes: [scene('a', [say('甲', '字'.repeat(30))])] }] })).toBe('')
  })

  it('flattens to text with act, scene, heading, action and 角色：台词', () => {
    expect(flattenScript(sample())).toBe('第一幕 · 相遇\n场 1 雪夜竹林\n外景 · 夜\n雪落竹林\n场 2 破庙\n狐妖：你来了。\n第二幕 · 真相\n场 3\n灯下')
    expect(flattenScript({ acts: [{ title: '', scenes: [scene('a', [action('字'.repeat(30_000))])] }] }).length).toBeGreaterThan(SPLIT_IDEA_LIMIT)  // 不截断
  })

  it('clamps the shot count to scenes x 2 within 4..12', () => {
    expect([1, 2, 3, 6, 7].map(splitShotCount)).toEqual([4, 4, 6, 12, 12])
  })

  it('creates unique scene ids, acts and scenes', () => {
    expect(newSceneId(sample())).toBe('s4')
    expect(newSceneId({ acts: [{ title: '', scenes: [scene('s2')] }] })).toBe('s1')
    const added = addScene(sample(), 1)!
    expect(added.payload.acts[1].scenes.map((item) => item.id)).toEqual(['s3', 's4'])
    expect(addScene({ acts: [] }, 0)!.payload.acts).toHaveLength(1)
    const act = addAct(sample())!
    expect(act.payload.acts).toHaveLength(3)
    expect(act.payload.acts[2].title).toBe('第三幕')
    expect([0, 10, 11, 20].map(actTitle)).toEqual(['第一幕', '第十一幕', '第十二幕', '第二十一幕'])
  })

  it('moves scenes within an act and across act boundaries, not past the ends', () => {
    const down = moveScene(sample(), 's1', 1)
    expect(down.acts[0].scenes.map((item) => item.id)).toEqual(['s2', 's1'])
    const across = moveScene(sample(), 's2', 1)
    expect(across.acts.map((act) => act.scenes.map((item) => item.id))).toEqual([['s1'], ['s2', 's3']])
    const up = moveScene(sample(), 's3', -1)
    expect(up.acts.map((act) => act.scenes.map((item) => item.id))).toEqual([['s1', 's2', 's3'], []])
    expect(canMoveScene(sample(), 's1', -1)).toBe(false)
    expect(canMoveScene(sample(), 's3', 1)).toBe(false)
    expect(canMoveScene(sample(), 's2', 1)).toBe(true)
    expect(removeScene(sample(), 's2').acts[0].scenes.map((item) => item.id)).toEqual(['s1'])
  })

  it('inserts and moves blocks', () => {
    const blocks = [action('a'), say('甲', 'b')]
    expect(insertBlock(blocks, 0, 'dialogue').map((item) => item.type)).toEqual(['action', 'dialogue', 'dialogue'])
    expect(insertBlock(blocks, -1, 'action')[0].text).toBe('')
    expect(moveBlock(blocks, 0, 1).map((item) => item.text)).toEqual(['b', 'a'])
    expect(moveBlock(blocks, 0, -1)).toBe(blocks)
  })

  it('replaces only the selected range and refuses when the text there has changed', () => {
    expect(replaceSelection('你来了。我等你三百年。', 0, 4, '你来了。', '你终于来了。')).toBe('你终于来了。我等你三百年。')
    expect(replaceSelection('你来了。你来了。', 4, 8, '你来了。', '好')).toBe('你来了。好')  // 第二处，不是第一处
    expect(replaceSelection('前面加了字你来了。', 0, 4, '你来了。', '好')).toBeNull()  // 位置变了，不去别处找
    expect(replaceSelection('完全不同', 0, 4, '你来了。', '好')).toBeNull()
  })

  it('gives blocks stable unique ids and leaves already-valid payloads untouched', () => {
    const fresh: ScriptPayload = { acts: [{ title: '', scenes: [scene('s1', [{ id: '', type: 'action', character: '', text: 'a' }, { id: 'x', type: 'action', character: '', text: 'b' }, { id: 'x', type: 'action', character: '', text: 'c' }])] }] }
    const fixed = ensureBlockIds(fresh)
    const ids = fixed.acts[0].scenes[0].blocks.map((block) => block.id)
    expect(new Set(ids).size).toBe(3)
    expect(ids[1]).toBe('x')
    expect(ensureBlockIds(fixed)).toBe(fixed)
    expect(addScene({ acts: [] }, 0)!.payload.acts[0].scenes[0].blocks[0].id).toMatch(/^b-/)
  })
})
