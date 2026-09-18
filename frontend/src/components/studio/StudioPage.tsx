import { useEffect, useMemo, useState } from 'react'
import { PageHeader } from '../ui/PageHeader'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { GenerationGate, SeriesAsset, WorkspaceSummary } from '../../api/series'
import { useSeriesProject } from '../../hooks/useSeriesProject'
import { useWorkflowRun } from '../../hooks/useWorkflowRun'
import { useJobs } from '../../hooks/useJobs'
import StoryboardPanel from './StoryboardPanel'
import BiblePanel from './BiblePanel'
import ScriptPanel from './ScriptPanel'
import DirectorPanel from './DirectorPanel'
import AssetPanel from './AssetPanel'
import ApprovalBar from './ApprovalBar'
import GeneratePanel from './GeneratePanel'
import WorkflowDAG from './WorkflowDAG'
import UsageSummary from './UsageSummary'
import TimelinePanel from './TimelinePanel'
import PublishPanel from './PublishPanel'
import ReferenceAnalysisPanel from './ReferenceAnalysisPanel'
import EvalPanel from './EvalPanel'
import './studio.css'

type ThemeMode = 'dark' | 'light'

type StageKey = 'bible' | 'outline' | 'script' | 'director' | 'assets' | 'references' | 'storyboard' | 'approval' | 'generate' | 'timeline' | 'compose' | 'publish' | 'eval'

const STAGES: Array<{ key: StageKey; label: string }> = [
  { key: 'bible', label: '企划 Bible' },
  { key: 'outline', label: '大纲' },
  { key: 'script', label: '剧本' },
  { key: 'director', label: '导演/复审' },
  { key: 'assets', label: '资产' },
  { key: 'references', label: '参考拉片' },
  { key: 'storyboard', label: '分镜' },
  { key: 'approval', label: '审批' },
  { key: 'generate', label: '生成' },
  { key: 'timeline', label: '剪辑' },
  { key: 'compose', label: '合成' },
  { key: 'publish', label: '终检/发布包' },
  { key: 'eval', label: '质量 Eval' },
]

const ACTIVE_JOB_STATUSES = new Set(['pending', 'leased', 'running'])

export default function StudioPage({
  projectId,
  theme,
  onToggleTheme,
}: {
  projectId: string
  theme: ThemeMode
  onToggleTheme: () => void
}) {
  const { project, storyboard, loading, error, reload } = useSeriesProject(projectId)
  const { run, reload: reloadRun } = useWorkflowRun(projectId, true)
  const { jobs, reload: reloadJobs, retry: retryJob, cancel: cancelJob } = useJobs(projectId, true)
  const [assets, setAssets] = useState<SeriesAsset[]>([])
  const [gate, setGate] = useState<GenerationGate | null>(null)
  const [selectedStage, setSelectedStage] = useState<StageKey | null>(null)
  const [summary, setSummary] = useState<WorkspaceSummary | null>(null)

  const firstEpisodeId = project?.episodes?.[0]?.id

  const reloadAssets = async () => {
    try {
      const list = await seriesApi.listAssets(projectId)
      setAssets(Array.isArray(list) ? list : [])
    } catch (e) {
      console.error('资产加载失败', e)
    }
  }

  const reloadGate = async () => {
    if (!firstEpisodeId) return
    try {
      setGate(await seriesApi.getGenerationGate(firstEpisodeId))
    } catch {
      setGate(null)
    }
  }

  const reloadSummary = async () => {
    try { setSummary(await seriesApi.getWorkspaceSummary(projectId)) } catch { setSummary(null) }
  }

  useEffect(() => {
    void reloadAssets()
    void reloadSummary()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  useEffect(() => {
    void reloadGate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstEpisodeId])

  const hasShots = useMemo(
    () => (storyboard?.episodes || []).some((ep) => (ep.scenes || []).some((s) => (s.shots || []).length > 0)),
    [storyboard],
  )

  /** 当前阶段推断：无分镜→大纲，无资产→资产，未审批→审批，有进行中任务→生成，全部完成→合成 */
  const currentStage: StageKey = useMemo(() => {
    const episodeStatuses = (project?.episodes || []).map((episode) => episode.status)
    if (
      run?.steps?.some(
        (step) => step.node_type.includes('.bible.') && step.status !== 'succeeded' && step.status !== 'skipped',
      )
    ) return 'bible'
    if (!hasShots && episodeStatuses.every((status) => !status || status === 'draft')) return 'outline'
    if (!hasShots && episodeStatuses.includes('script_review')) return 'script'
    if (assets.length === 0) return 'assets'
    if (!hasShots) return 'storyboard'
    if (gate && !gate.ok) return 'approval'
    if (jobs.some((j) => ACTIVE_JOB_STATUSES.has(j.status))) return 'generate'
    if (jobs.some((j) => j.operation === 'generate_video' && j.status === 'succeeded') && !jobs.some((j) => j.operation === 'render_episode' && j.status === 'succeeded')) return 'timeline'
    if (jobs.length > 0 && jobs.every((j) => j.status === 'succeeded')) return 'compose'
    return 'storyboard'
  }, [hasShots, assets.length, gate, jobs, project?.episodes, run?.steps])

  const stage = selectedStage ?? currentStage

  const refreshAll = () => {
    void reload()
    void reloadAssets()
    void reloadGate()
    void reloadJobs()
    void reloadRun()
    void reloadSummary()
  }

  const workspaceMode = summary?.workspace_mode || String(project?.settings?.workspace_mode || 'professional')
  const switchMode = async (mode: 'quick' | 'professional') => {
    try {
      await seriesApi.setWorkspaceMode(projectId, mode)
      await Promise.all([reload(), reloadSummary()])
      toast(mode === 'professional' ? '已进入专业工作台，所有项目数据已保留' : '已切换快速模式', 'success')
    } catch (e) { toast(e instanceof Error ? e.message : '模式切换失败', 'error') }
  }

  const onRunAction = async (action: () => Promise<unknown>, okMsg: string) => {
    try {
      await action()
      toast(okMsg, 'success')
      refreshAll()
    } catch (e) {
      toast(e instanceof Error ? e.message : '操作失败', 'error')
    }
  }

  return (
    <div className="studio">
      <PageHeader title={project?.title || '短剧工作台'} subtitle="系列视频项目" theme={theme} onToggleTheme={onToggleTheme} variant="sub" />
      <main className="studio__main">
        {run?.planner_model && (
          <div className="studio__muted" style={{ fontSize: 12, marginBottom: 8 }}>
            规划器：{run.planner_model}
            {run.planner_model === 'rule-based' ? '（规则规划）' : '（AI 规划）'}
          </div>
        )}
        <section className="studio__card" style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
          <b>{workspaceMode === 'quick' ? '快速模式' : '专业模式'}</b>
          <span className="studio__badge">阶段 {summary?.stage || currentStage}</span>
          <span className="studio__muted">进度 {summary?.progress.completed || 0}/{summary?.progress.total || 0}</span>
          <span className="studio__muted">预算 ¥{summary?.budget.spent_cny || 0} / {summary?.budget.limit_cny ?? '未设'}</span>
          <span className="studio__muted">Stale {summary?.stale_impact_count || 0}</span>
          <span className="studio__muted">QA 失败 {summary?.qa_failed_count || 0}</span>
          <span className="studio__muted">待审批 {summary?.pending_approval_count || 0}</span>
          <span className={summary?.job_error_count ? 'studio__error' : 'studio__muted'}>任务错误 {summary?.job_error_count || 0}</span>
          <button className="studio__btn" style={{ marginLeft: 'auto' }} onClick={() => void switchMode(workspaceMode === 'quick' ? 'professional' : 'quick')}>
            切换到{workspaceMode === 'quick' ? '专业模式' : '快速模式'}
          </button>
        </section>
        {workspaceMode === 'professional' && <nav className="studio-nav">
          {STAGES.map((s, i) => (
            <button
              key={s.key}
              className={`studio-nav__item${stage === s.key ? ' studio-nav__item--active' : ''}`}
              onClick={() => setSelectedStage(s.key)}
              title={s.key === currentStage ? '当前推断阶段' : undefined}
            >
              <span className="studio-nav__index">{i + 1}</span>
              {s.label}
              {s.key === currentStage && stage !== s.key ? ' ·' : ''}
            </button>
          ))}
        </nav>}

        {loading && <div className="studio__muted">加载中…</div>}
        {error && <div className="studio__error">{error}</div>}

        {!loading && project && (
          <>
            {workspaceMode === 'quick' && (
              <section className="studio__card">
                <div className="studio__section-title" style={{ marginTop: 0 }}>快速创作</div>
                <p><b>故事：</b>{String(project.settings?.story || run?.goal || '尚未填写')}</p>
                <p><b>风格：</b>{String(project.settings?.style || '默认')}</p>
                <p className="studio__muted">{project.episodes?.length || 0} 集 · 单集 {Number(project.settings?.target_duration_ms || 90000) / 1000} 秒</p>
                <p className="studio__muted">自动生成的剧本、分镜、候选和产物均写入正式版本；切换专业模式后可逐镜头继续编辑。</p>
                {gate && !gate.ok && <div className="studio__error">关键审批尚未完成：{gate.reasons.join('；')}</div>}
              </section>
            )}
            {workspaceMode === 'quick' && (
              <ApprovalBar projectId={projectId} storyboard={storyboard} assets={assets} gate={gate} onChanged={refreshAll} />
            )}
            {workspaceMode === 'professional' && <>
            {stage === 'bible' && <BiblePanel projectId={projectId} onChanged={refreshAll} />}

            {stage === 'outline' && (
              <section className="studio__card">
                <div className="studio__section-title" style={{ marginTop: 0 }}>
                  大纲
                </div>
                <p className="studio__muted">
                  项目目标：{run?.goal || project.settings?.goal ? String(run?.goal || project.settings?.goal) : '（未填写）'}
                </p>
                <p className="studio__muted">
                  当前还没有结构化剧本。先确定本集目标、冲突和结尾悬念，再进入资产与导演分镜阶段。
                </p>
                <button className="studio__btn" onClick={() => setSelectedStage('script')}>
                  前往剧本
                </button>
              </section>
            )}

            {stage === 'assets' && (
              <AssetPanel projectId={projectId} assets={assets} jobs={jobs} onChanged={refreshAll} />
            )}

            {stage === 'script' && (
              <ScriptPanel projectId={projectId} episodes={project.episodes || []} onChanged={refreshAll} />
            )}

            {stage === 'director' && (
              <DirectorPanel episodes={project.episodes || []} onChanged={refreshAll} />
            )}

            {stage === 'storyboard' && (
              <StoryboardPanel project={project} storyboard={storyboard} onChanged={refreshAll} />
            )}

            {stage === 'references' && (
              <ReferenceAnalysisPanel projectId={projectId} storyboard={storyboard} onChanged={refreshAll} />
            )}

            {stage === 'approval' && (
              <ApprovalBar
                projectId={projectId}
                storyboard={storyboard}
                assets={assets}
                gate={gate}
                onChanged={refreshAll}
              />
            )}

            {stage === 'generate' && (
              <>
                <GeneratePanel
                  run={run}
                  jobs={jobs}
                  storyboard={storyboard}
                  onRetryJob={(id) => onRunAction(() => retryJob(id), '已重新排队')}
                  onCancelJob={(id) => onRunAction(() => cancelJob(id), '已取消')}
                  onChanged={refreshAll}
                />
                <div className="studio__section-title">Workflow</div>
                {run ? (
                  <WorkflowDAG
                    run={run}
                    onPause={() => onRunAction(() => seriesApi.pauseRun(run.id), '已暂停')}
                    onResume={() => onRunAction(() => seriesApi.resumeRun(run.id), '已继续')}
                    onRetryStep={(stepId) => onRunAction(() => seriesApi.retryStep(stepId), '已重试')}
                  />
                ) : (
                  <div className="studio__muted">暂无 workflow run。</div>
                )}
              </>
            )}

            {stage === 'timeline' && (
              <TimelinePanel episodes={project.episodes || []} onChanged={refreshAll} />
            )}

            {stage === 'compose' && (
              <>
                <section className="studio__card">
                  <div className="studio__section-title" style={{ marginTop: 0 }}>
                    合成
                  </div>
                  <p className="studio__muted">全部镜头任务已完成，以下为生成产物。合成成片能力由后端 workflow 提供。</p>
                </section>
                <div className="studio__section-title">产物</div>
                <GeneratePanel run={run} jobs={jobs.filter((j) => j.status === 'succeeded')} storyboard={storyboard} resultsOnly />
                <div className="studio__section-title">费用</div>
                <UsageSummary projectId={projectId} />
              </>
            )}

            {stage === 'publish' && (
              <PublishPanel projectId={projectId} episodes={project.episodes || []} onChanged={refreshAll} />
            )}

            {stage === 'eval' && <EvalPanel projectId={projectId} />}
            </>}
          </>
        )}
      </main>
    </div>
  )
}
