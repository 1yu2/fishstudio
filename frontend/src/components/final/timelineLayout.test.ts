import { describe, expect, it } from 'vitest'
import { blockStyle, clock, renderActive, seekSeconds } from './timelineLayout'
import type { FinalState } from '../../api/final'

describe('timeline layout', () => {
  it('lays blocks out proportionally to the total duration', () => {
    expect(blockStyle(0, 3000, 12000)).toEqual({ left: '0%', width: '25%' })
    expect(blockStyle(6000, 12000, 12000)).toEqual({ left: '50%', width: '50%' })
    expect(blockStyle(1000, 1500, 3000)).toEqual({ left: '33.333%', width: '16.667%' })
  })

  it('clamps blocks into the timeline and tolerates an empty one', () => {
    expect(blockStyle(-500, 20000, 10000)).toEqual({ left: '0%', width: '100%' })
    expect(blockStyle(0, 1000, 0)).toEqual({ left: '0%', width: '0%' })
  })

  it('maps a click fraction to seconds', () => {
    expect(seekSeconds(0.5, 52000)).toBe(26)
    expect(seekSeconds(1.4, 10000)).toBe(10)
    expect(seekSeconds(-1, 10000)).toBe(0)
  })

  it('formats clock and detects active renders', () => {
    expect(clock(52000)).toBe('0:52')
    expect(clock(125000)).toBe('2:05')
    const state = (status: string) => ({ latest_render: { status } }) as unknown as FinalState
    expect(renderActive(state('running'))).toBe(true)
    expect(renderActive(state('queued'))).toBe(true)
    expect(renderActive(state('succeeded'))).toBe(false)
    expect(renderActive(null)).toBe(false)
  })
})
