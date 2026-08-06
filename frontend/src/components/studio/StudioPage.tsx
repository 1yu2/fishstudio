import { useEffect, useMemo, useState } from 'react'
import { PageHeader } from '../ui/PageHeader'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { GenerationGate, SeriesAsset } from '../../api/series'
import { useSeriesProject } from '../../hooks/useSeriesProject'
import { useWorkflowRun } from '../../hooks/useWorkflowRun'
import { useJobs } from '../../hooks/useJobs'
import StoryboardPanel from './StoryboardPanel'
import AssetPanel from './AssetPanel'
import ApprovalBar from './ApprovalBar'
import GeneratePanel from './GeneratePanel'
import WorkflowDAG from './WorkflowDAG'
import UsageSummary from './UsageSummary'
import './studio.css'

type ThemeMode = 'dark' | 'light'

type StageKey = 'outline' | 'assets' | 'storyboard' | 'approval' | 'generate' | 'compose'

const STAGES: Array<{ key: StageKey; label: string }> = [
  { key: 'outline', label: '大纲' },
  { key: 'assets', label: '资产' },
  { key: 'storyboard', label: '分镜' },
  { key: 'approval', label: '审批' },
  { key: 'generate', label: '生成' },
  { key: 'compose', label: '合成' },
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

  useEffect(() => {
    void reloadAssets()
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
    if (!hasShots) return 'outline'
    if (assets.length === 0) return 'assets'
    if (gate && !gate.ok) return 'approval'
    if (jobs.some((j) => ACTIVE_JOB_STATUSES.has(j.status))) return 'generate'
    if (jobs.length > 0 && jobs.every((j) => j.status === 'succeeded')) return 'compose'
    return 'storyboard'
  }, [hasShots, assets.length, gate, jobs])

  const stage = selectedStage ?? currentStage

  const refreshAll = () => {
    void reload()
    void reloadAssets()
    void reloadGate()
    void reloadJobs()
    void reloadRun()
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
        <nav className="studio-nav">
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
        </nav>

        {loading && <div className="studio__muted">加载中…</div>}
        {error && <div className="studio__error">{error}</div>}

        {!loading && project && (
          <>
            {stage === 'outline' && (
              <section className="studio__card">
                <div className="studio__section-title" style={{ marginTop: 0 }}>
                  大纲
                </div>
                <p className="studio__muted">
                  项目目标：{run?.goal || project.settings?.goal ? String(run?.goal || project.settings?.goal) : '（未填写）'}
                </p>
                <p className="studio__muted">
                  当前还没有分镜内容。可先在下方「分镜」阶段导入旧文本分镜，或在画板中与助手讨论大纲后生成结构化分镜。
                </p>
                <button className="studio__btn" onClick={() => setSelectedStage('storyboard')}>
                  前往分镜
                </button>
              </section>
            )}

            {stage === 'assets' && (
              <AssetPanel projectId={projectId} assets={assets} jobs={jobs} onChanged={refreshAll} />
            )}

            {stage === 'storyboard' && (
              <StoryboardPanel project={project} storyboard={storyboard} onChanged={refreshAll} />
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
          </>
        )}
      </main>
    </div>
  )
}
