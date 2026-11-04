import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Loader2, Music, Upload } from 'lucide-react'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { finalApi, type FinalIssue } from '../../api/final'
import { paths } from '../../routePaths'
import { ProtectedVideo } from '../common/ProtectedMedia'
import type { ProjectOutletContext } from '../project/outletContext'
import { confirmDialog } from '../ui/ConfirmDialog'
import { toast } from '../ui/Toast'
import QcCard, { timeLabel } from './QcCard'
import Timeline from './Timeline'
import { useFinal } from './useFinal'
import './final.css'

const STALE_TEXT = '素材已变更，需要重新渲染'

/** 一条问题 / 警告：缺视频、静帧去分镜，配音相关去配音。 */
function IssueItem({ issue, canvasId, kind }: { issue: FinalIssue; canvasId: string; kind: 'problem' | 'warning' }) {
  const voice = issue.code === 'voice_overflow' || issue.code === 'missing_voice' || issue.code === 'voice_unreadable'
  const step = voice ? 'voice' : 'storyboard'
  return (
    <li className={`fn-issue fn-issue--${kind}`}>
      <AlertTriangle size={14} aria-hidden="true" />
      <span>{issue.message}</span>
      <Link className="fn-link" to={paths.projectStep(canvasId, step)}>{voice ? '去配音' : '去分镜'}</Link>
    </li>
  )
}

/** 第 5 步：成片页。预览 + 成片设置 + 只读时间线 + 问题列表 + 渲染 / 导出。 */
export default function FinalPage() {
  const { canvasId = '' } = useParams()
  const outlet = useOutletContext<ProjectOutletContext | null>()
  const setFooter = outlet?.setFooter
  const refreshSummary = outlet?.refreshSummary
  const data = useFinal(canvasId, refreshSummary)
  const { state, settings } = data
  const videoRef = useRef<HTMLVideoElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [currentMs, setCurrentMs] = useState(0)

  const lastSuccess = state?.last_success ?? null
  const latest = state?.latest_render ?? null
  const active = !!latest && ['queued', 'running', 'pending', 'leased'].includes(latest.status)
  const failed = latest?.status === 'failed'
  const stale = !!state?.stale
  const blocked = (state?.problems.length ?? 0) > 0
  const qc = lastSuccess?.qc ?? null
  const qcFailed = qc?.status === 'failed'
  const qcReason = qcFailed ? qc?.issues.find((issue) => issue.level === 'blocking')?.message ?? '请重新渲染' : ''
  const exportable = !!lastSuccess?.url && !qcFailed
  // 检查没通过 / 有提醒时，播放器不提供原生下载入口（只能走“导出成片”按钮和它的确认）
  const guard = qc?.status === 'failed' || qc?.status === 'warning'
    ? { controlsList: qcFailed ? 'nodownload noplaybackrate' : 'nodownload', disablePictureInPicture: qcFailed, onContextMenu: (event: React.MouseEvent) => event.preventDefault() }
    : {}

  const { flush, refresh } = data
  const exportFinal = useCallback(async () => {
    // 先等设置保存完，再重新取一次最新状态：以服务端当下的“是否过期”为准，不用页面上可能过时的值
    let fresh
    try {
      await flush()
      fresh = await refresh()
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : '获取最新状态失败', 'error')
      return
    }
    const url = fresh.last_success?.url
    if (!url) return
    // 出片检查没通过不能导出（门禁在前端：以刚取到的最新结果为准）
    const freshQc = fresh.last_success?.qc
    if (freshQc?.status === 'failed') { toast(`出片检查没通过：${freshQc.issues.find((issue) => issue.level === 'blocking')?.message ?? '请重新渲染'}`, 'error'); return }
    if (freshQc?.status === 'warning') {
      const lines = freshQc.issues.map((issue) => `${timeLabel(issue) ? `${timeLabel(issue)} ` : ''}${issue.message}`)
      if (!await confirmDialog({ title: `出片检查有 ${lines.length} 条提醒，仍要导出吗？`, description: lines.slice(0, 6).join('；') + (lines.length > 6 ? `；等共 ${lines.length} 条` : ''), confirmText: '仍要导出' })) return
    }
    if (fresh.stale && !await confirmDialog({ title: '当前成片不是最新素材，仍要下载吗？', description: '素材在上次渲染之后有变化，下载的是旧版本。', confirmText: '仍要下载' })) return
    try { await finalApi.download(url, `成片-${canvasId}.mp4`) }
    catch (reason) { toast(reason instanceof Error ? reason.message : '下载失败', 'error') }
  }, [flush, refresh, canvasId])

  // 底栏：没有渲染结果时不能导出
  const exportRef = useRef(exportFinal)
  exportRef.current = exportFinal
  const hint = blocked ? state?.problems[0]?.message : stale ? STALE_TEXT : failed ? '上次渲染失败，可以重试' : undefined
  useEffect(() => {
    if (!state) return
    const note = exportable ? undefined : qcFailed ? `出片检查没通过：${qcReason}` : '先渲染一次'
    setFooter?.({ label: '导出成片', onClick: () => void exportRef.current(), disabled: !exportable, note, hint })
  }, [setFooter, state, exportable, hint, qcFailed, qcReason])
  useEffect(() => () => setFooter?.(null), [setFooter])

  const seek = useCallback((seconds: number) => {
    setCurrentMs(seconds * 1000)
    if (videoRef.current) videoRef.current.currentTime = seconds
  }, [])

  const issues = useMemo(() => state ? [...state.problems.map((issue) => ({ issue, kind: 'problem' as const })), ...state.warnings.map((issue) => ({ issue, kind: 'warning' as const }))] : [], [state])

  if (data.error) return <div className="fn-message" role="alert">{data.error}</div>
  if (!state) return <div className="fn-message" aria-busy="true">成片加载中…</div>

  const shotCount = state.timeline.shots.length
  const renderLabel = lastSuccess || latest ? '重新渲染预览' : '渲染预览'
  return (
    <div className="fn">
      <div className="fn-top">
        <section className="fn-preview" aria-label="预览">
          {lastSuccess?.url
            ? <ProtectedVideo ref={videoRef} key={lastSuccess.url} src={lastSuccess.url} controls playsInline {...guard} onTimeUpdate={(event) => setCurrentMs(event.currentTarget.currentTime * 1000)} />
            : <div className="fn-preview__empty">还没有渲染过</div>}
          <div className="fn-preview__meta">
            {shotCount} 个镜头 · {state.timeline.voices.length} 句配音{state.timeline.bgm ? ' · 有背景音乐' : ''}
          </div>
        </section>

        <aside className="fn-settings" aria-label="成片设置">
          <h2>成片设置</h2>
          <fieldset className="fn-group">
            <legend>字幕</legend>
            <label className="fn-check"><input type="checkbox" checked={settings.burn_subtitles} onChange={(event) => data.updateSettings({ burn_subtitles: event.target.checked })} />烧录到画面</label>
            <label className="fn-check"><input type="checkbox" checked={settings.export_srt} onChange={(event) => data.updateSettings({ export_srt: event.target.checked })} />同时导出 SRT 文件</label>
          </fieldset>
          <fieldset className="fn-group">
            <legend>背景音乐</legend>
            <div className="fn-bgm">
              <Music size={14} aria-hidden="true" />
              <span className="fn-bgm__name">{data.bgmName ?? '没有背景音乐'}</span>
              <input ref={fileRef} type="file" hidden accept=".mp3,.wav,.m4a,.aac,audio/*" aria-label="选择背景音乐文件"
                onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void data.uploadBgm(file) }} />
              <button type="button" className="fn-btn" disabled={data.uploading} onClick={() => fileRef.current?.click()}>
                {data.uploading ? <Loader2 size={13} className="fn-spin" aria-hidden="true" /> : <Upload size={13} aria-hidden="true" />}{settings.bgm_artifact_id ? '更换' : '上传'}
              </button>
              {settings.bgm_artifact_id && <button type="button" className="fn-btn" onClick={data.removeBgm}>移除</button>}
            </div>
            <label className="fn-slider">音量 {settings.bgm_volume_db} dB
              <input type="range" min={-40} max={0} step={1} value={settings.bgm_volume_db} aria-label="背景音乐音量" disabled={!settings.bgm_artifact_id}
                onChange={(event) => data.updateSettings({ bgm_volume_db: Number(event.target.value) })} />
            </label>
            <label className="fn-check"><input type="checkbox" checked={settings.ducking} disabled={!settings.bgm_artifact_id} onChange={(event) => data.updateSettings({ ducking: event.target.checked })} />有人声时自动压低音乐</label>
            <label className="fn-check"><input type="checkbox" checked={settings.fades} disabled={!settings.bgm_artifact_id} onChange={(event) => data.updateSettings({ fades: event.target.checked })} />开头结尾淡入淡出</label>
          </fieldset>
          <fieldset className="fn-group">
            <legend>导出</legend>
            <label className="fn-inline">清晰度
              <select value={settings.resolution} aria-label="清晰度" onChange={(event) => data.updateSettings({ resolution: event.target.value as '720p' | '1080p' })}>
                <option value="720p">720p · MP4</option>
                <option value="1080p">1080p · MP4</option>
              </select>
            </label>
            <label className="fn-check"><input type="checkbox" disabled checked readOnly />自动加入“AI 生成”标识（平台要求）</label>
          </fieldset>
          <QcCard qc={qc} hasRender={!!lastSuccess} rendering={active || data.rendering} onSeek={seek} />
        </aside>
      </div>

      <Timeline state={state} currentMs={currentMs} onSeek={seek} markers={active ? [] : qc?.issues ?? []} />

      {issues.length > 0 && (
        <ul className="fn-issues" aria-label="问题与提醒">
          {issues.map(({ issue, kind }, index) => <IssueItem key={`${issue.code}-${issue.shot_index ?? ''}-${index}`} issue={issue} canvasId={canvasId} kind={kind} />)}
        </ul>
      )}

      <section className="fn-actions" aria-label="渲染与导出">
        {stale && !active && <p className="fn-stale" role="status">{STALE_TEXT}</p>}
        {active && latest && (
          <div className="fn-progress" role="progressbar" aria-label="渲染进度" aria-valuenow={latest.progress} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${latest.progress}%` }} />
            <em>{latest.status === 'queued' ? '排队中…' : `渲染中 ${latest.progress}%`}</em>
          </div>
        )}
        {failed && !active && (
          <p className="fn-error" role="alert">渲染失败：{latest?.error || '请重试'}</p>
        )}
        {data.renderError && !failed && <p className="fn-error" role="alert">{data.renderError}</p>}
        <div className="fn-actions__row">
          <button type="button" className="fn-btn" disabled={blocked || active || data.rendering} onClick={() => void data.render()}>
            {(active || data.rendering) && <Loader2 size={13} className="fn-spin" aria-hidden="true" />}{failed && !active ? '重试渲染' : renderLabel}
          </button>
          {blocked && <span className="fn-reason">先解决上面的问题再渲染</span>}
          <button type="button" className="fn-btn fn-btn--primary" disabled={!exportable} onClick={() => void exportFinal()}>导出成片</button>
          {!exportable && <span className="fn-reason">{qcFailed ? `出片检查没通过：${qcReason}，建议重新渲染` : '先渲染一次'}</span>}
          {lastSuccess?.srt_url && <button type="button" className="fn-link" onClick={() => void finalApi.download(lastSuccess.srt_url!, `字幕-${canvasId}.srt`).catch((reason) => toast(reason instanceof Error ? reason.message : '下载失败', 'error'))}>下载 SRT 字幕</button>}
        </div>
      </section>
    </div>
  )
}
