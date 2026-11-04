import type { MouseEvent } from 'react'
import { ProtectedImage } from '../common/ProtectedMedia'
import type { FinalState, QcIssue } from '../../api/final'
import { blockStyle, clock, seekSeconds } from './timelineLayout'

interface Props {
  state: FinalState
  /** 播放位置（毫秒），画播放头 */
  currentMs: number
  onSeek: (seconds: number) => void
  /** 出片检查发现的问题片段（只读小标记）：黑屏 / 冻帧 / 静音 */
  markers?: QcIssue[]
}

export const MARKER_LABEL: Record<string, string> = { black_segments: '黑屏', frozen_segments: '画面静止', silent_segments: '静音' }

/** 只读多轨时间线：画面 / 配音 / 字幕 / BGM 四条轨道，块按时间比例排布；点击轨道跳转播放位置。 */
export default function Timeline({ state, currentMs, onSeek, markers = [] }: Props) {
  const segments = markers.filter((item) => item.code in MARKER_LABEL && item.start_ms !== undefined && item.end_ms !== undefined)
  const total = state.total_ms
  const { shots, voices, subtitles, bgm } = state.timeline
  const click = (event: MouseEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    if (rect.width > 0) onSeek(seekSeconds((event.clientX - rect.left) / rect.width, total))
  }
  const track = (name: string, label: string, content: React.ReactNode, empty: string, isEmpty: boolean) => (
    <div className="fn-track">
      <span className="fn-track__label">{label}</span>
      <div className="fn-track__lane" data-track={name} role="group" aria-label={`${label}轨道`} onClick={click}>
        {isEmpty ? <span className="fn-track__empty">{empty}</span> : content}
      </div>
    </div>
  )
  return (
    <section className="fn-timeline" aria-label="时间线">
      <header className="fn-timeline__head">
        <h2>时间线（只读，调整顺序和入出点请回到分镜）</h2>
        <span className="fn-timeline__total">{clock(total)} · {Math.round(total / 1000)} 秒</span>
      </header>
      <div className="fn-timeline__body">
        {total > 0 && <div className="fn-playhead" style={{ left: `calc(56px + (100% - 56px) * ${(currentMs / total).toFixed(5)})` }} data-testid="playhead" data-fraction={(currentMs / total).toFixed(5)} />}
        {track('video', '画面', shots.map((shot) => (
          <div key={shot.shot_id} className={`fn-block fn-block--shot${shot.has_video ? '' : ' fn-block--missing'}`} style={blockStyle(shot.start_ms, shot.end_ms, total)} title={`${shot.index + 1} · ${shot.title}${shot.still ? '（静帧）' : ''}`}>
            {shot.thumbnail_url && <ProtectedImage src={shot.thumbnail_url} alt="" />}
            <span className="fn-block__tag">{shot.index + 1}</span>
          </div>
        )), '还没有镜头', shots.length === 0)}
        {track('voice', '配音', voices.map((voice) => (
          <div key={`${voice.node_id}-${voice.start_ms}`} className={`fn-block fn-block--voice fn-block--${voice.kind}`} style={blockStyle(voice.start_ms, voice.end_ms, total)} title={voice.text}>
            <span className="fn-block__text">{voice.speaker || '配音'}</span>
          </div>
        )), '还没有配音', voices.length === 0)}
        {track('subtitle', '字幕', subtitles.map((cue, index) => (
          <div key={`${cue.start_ms}-${index}`} className="fn-block fn-block--subtitle" style={blockStyle(cue.start_ms, cue.end_ms, total)} title={cue.text}>
            <span className="fn-block__text">{cue.text}</span>
          </div>
        )), '没有字幕', subtitles.length === 0)}
        {track('bgm', 'BGM', bgm && (
          <div className="fn-block fn-block--bgm" style={blockStyle(bgm.start_ms, bgm.end_ms, total)}>
            <span className="fn-block__text">{bgm.name}</span>
          </div>
        ), '没有背景音乐', !bgm)}
        {segments.length > 0 && track('qc', '检查', segments.map((item, index) => (
          <div key={`${item.code}-${item.start_ms}-${index}`} data-testid="qc-marker" className={`fn-marker fn-marker--${item.code.replace('_segments', '')}`} style={blockStyle(item.start_ms ?? 0, item.end_ms ?? 0, total)} title={`${MARKER_LABEL[item.code]} · ${item.message}`} />
        )), '', false)}
      </div>
    </section>
  )
}
