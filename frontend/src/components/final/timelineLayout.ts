import type { FinalSettings, FinalState } from '../../api/final'

/** 时间线上的块：left / width 按总时长的百分比（保留 3 位小数，避免浮点抖动）。 */
export function blockStyle(startMs: number, endMs: number, totalMs: number): { left: string; width: string } {
  if (totalMs <= 0) return { left: '0%', width: '0%' }
  const pct = (value: number) => `${Math.round(Math.max(0, Math.min(100, (value / totalMs) * 100)) * 1000) / 1000}%`
  const left = Math.max(0, Math.min(startMs, totalMs))
  const right = Math.max(left, Math.min(endMs, totalMs))
  return { left: pct(left), width: pct(right - left) }
}

/** 点击轨道的横向比例（0..1）→ 时间（秒）。 */
export function seekSeconds(fraction: number, totalMs: number): number {
  return (Math.max(0, Math.min(1, fraction)) * totalMs) / 1000
}

/** 56000 → “0:56”；不足 1 秒按 0:00。 */
export function clock(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

export const ACTIVE_RENDER = new Set(['queued', 'running', 'pending', 'leased'])

export function renderActive(state: FinalState | null): boolean {
  return !!state?.latest_render && ACTIVE_RENDER.has(state.latest_render.status)
}

export const DEFAULT_SETTINGS: FinalSettings = {
  burn_subtitles: true, export_srt: true, bgm_artifact_id: null, bgm_volume_db: -18, ducking: true, fades: true, resolution: '720p',
}


