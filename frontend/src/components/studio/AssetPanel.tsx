import { useCallback, useEffect, useState } from 'react'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { AssetType, CloneVoiceQuote, Job, SeriesAsset, UploadedArtifact } from '../../api/series'
import { ProtectedAudio } from '../common/ProtectedMedia'
import './studio.css'

const TYPE_LABELS: Record<AssetType, string> = {
  character: '角色',
  style: '风格',
  scene: '场景',
  voice: '音色',
}

const TYPE_ORDER: AssetType[] = ['character', 'style', 'scene', 'voice']

function parseTags(input: string): string[] {
  return input
    .split(/[,，\s]+/)
    .map((t) => t.trim())
    .filter(Boolean)
}

function assetVersion(asset: SeriesAsset): number {
  return asset.version ?? asset.current_version ?? 1
}

function metaString(asset: SeriesAsset, key: string): string {
  const value = asset.provider_meta?.[key]
  return typeof value === 'string' ? value : ''
}

function AssetCreateForm({ onCreate, creating }: { onCreate: (input: { type: AssetType; name: string; description: string; tags: string[] }) => void; creating: boolean }) {
  const [type, setType] = useState<AssetType>('character')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [tags, setTags] = useState('')

  const submit = () => {
    if (!name.trim() || creating) return
    onCreate({ type, name: name.trim(), description: description.trim(), tags: parseTags(tags) })
    setName('')
    setDescription('')
    setTags('')
  }

  return (
    <div className="studio__card asset-form">
      <div>
        <label>类型</label>
        <select className="studio__select" value={type} onChange={(e) => setType(e.target.value as AssetType)}>
          {TYPE_ORDER.map((t) => (
            <option key={t} value={t}>
              {TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label>名称</label>
        <input className="studio__input" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：女主·林晚" />
      </div>
      <div>
        <label>描述</label>
        <input className="studio__input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="外形/音色/风格描述" />
      </div>
      <div>
        <label>标签（逗号分隔）</label>
        <input className="studio__input" value={tags} onChange={(e) => setTags(e.target.value)} />
      </div>
      <button className="studio__btn studio__btn--primary" onClick={submit} disabled={creating || !name.trim()}>
        新建资产
      </button>
    </div>
  )
}

function AssetGroups({ assets, action }: { assets: SeriesAsset[]; action?: (a: SeriesAsset) => React.ReactNode }) {
  return (
    <>
      {TYPE_ORDER.map((t) => {
        const group = assets.filter((a) => a.type === t)
        if (group.length === 0) return null
        return (
          <div key={t} className="asset-group">
            <div className="studio__section-title">{TYPE_LABELS[t]}</div>
            <div className="asset-grid">
              {group.map((a) => (
                <div key={a.id} className="asset-card">
                  <div className="asset-card__name">{a.name}</div>
                  {a.description && <div className="studio__muted">{a.description}</div>}
                  <div className="asset-card__badges">
                    <span className={`studio__badge studio__badge--${a.status}`}>{a.status || 'draft'}</span>
                    <span className="studio__badge">v{assetVersion(a)}</span>
                    {(a.tags || []).map((tag) => (
                      <span key={tag} className="studio__badge">
                        #{tag}
                      </span>
                    ))}
                  </div>
                  {action && <div style={{ marginTop: 8 }}>{action(a)}</div>}
                </div>
              ))}
            </div>
          </div>
        )
      })}
    </>
  )
}

function VoiceCloneControls({
  projectId,
  asset,
  jobs,
  onChanged,
}: {
  projectId: string
  asset: SeriesAsset
  jobs: Job[]
  onChanged: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [uploaded, setUploaded] = useState<UploadedArtifact | null>(null)
  const [quote, setQuote] = useState<CloneVoiceQuote | null>(null)
  const [targetAsset, setTargetAsset] = useState<SeriesAsset | null>(null)
  const [busy, setBusy] = useState(false)
  const [approving, setApproving] = useState(false)

  const observedAssetId = targetAsset?.id ?? asset.id
  const latestJob = jobs.find(
    (job) => job.operation === 'clone_voice' && String(job.params?.asset_id || '') === observedAssetId,
  )
  const previewUrl = metaString(asset, 'preview_url') || latestJob?.result?.url || ''

  const requestQuote = async () => {
    if (!file || busy) return
    setBusy(true)
    try {
      let target = targetAsset ?? asset
      if (!targetAsset && asset.status === 'approved') {
        target = await seriesApi.createAssetVersion(asset.id, { provider_meta: {} })
        setTargetAsset(target)
        toast(`已创建「${asset.name}」v${assetVersion(target)}，旧版本保持已批准状态`, 'success')
      }
      const artifact = await seriesApi.uploadProjectAudio(projectId, file)
      const nextQuote = await seriesApi.getCloneVoiceQuote(projectId, target.id, artifact.artifact_id)
      setUploaded(artifact)
      setQuote(nextQuote)
    } catch (error) {
      toast(error instanceof Error ? error.message : '参考音频上传或报价失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  const confirmClone = async () => {
    if (!quote || !uploaded || busy) return
    setBusy(true)
    try {
      const result = await seriesApi.confirmCloneVoice(projectId, quote.voice_asset_id, {
        reference_audio_artifact_id: uploaded.artifact_id,
        confirmed_quote_id: quote.quote_id,
        voice_name: asset.name,
      })
      toast(result.status === 'exists' ? '该音色任务已存在' : '音色复刻任务已提交', 'success')
      setQuote(null)
      setUploaded(null)
      setFile(null)
      onChanged()
    } catch (error) {
      toast(error instanceof Error ? error.message : '音色复刻提交失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  const approvePreview = async () => {
    if (approving) return
    setApproving(true)
    try {
      await seriesApi.submitApproval({
        project_id: projectId,
        target_type: 'asset',
        target_id: asset.id,
        target_revision: assetVersion(asset),
        action: 'approve',
        comment: '已在资产面板试听确认',
      })
      toast('音色试听已确认并通过审批', 'success')
      onChanged()
    } catch (error) {
      toast(error instanceof Error ? error.message : '审批失败', 'error')
    } finally {
      setApproving(false)
    }
  }

  return (
    <div className="voice-clone">
      {previewUrl && (
        <div className="voice-clone__preview">
          <ProtectedAudio src={previewUrl} controls preload="metadata" />
        </div>
      )}
      {latestJob && (
        <div className="studio__muted">
          复刻任务：<span className={`studio__badge studio__badge--${latestJob.status}`}>{latestJob.status}</span>
          {latestJob.error ? ` · ${latestJob.error}` : ''}
        </div>
      )}
      {asset.status === 'preview_ready' ? (
        <button className="studio__btn studio__btn--primary studio__btn--sm" onClick={approvePreview} disabled={approving}>
          {approving ? '提交中…' : '试听通过并审批'}
        </button>
      ) : (
        <>
          <input
            className="voice-clone__file"
            type="file"
            accept="audio/*,.wav,.mp3,.m4a,.aac,.ogg,.flac,.wma"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null)
              setUploaded(null)
              setQuote(null)
            }}
          />
          <button className="studio__btn studio__btn--sm" onClick={requestQuote} disabled={!file || busy}>
            {busy && !quote ? '处理中…' : asset.status === 'approved' ? '新建版本并获取报价' : '上传并获取报价'}
          </button>
        </>
      )}
      {quote && uploaded && (
        <div className="voice-clone__quote">
          <div>
            {quote.provider} · {quote.currency} {quote.price}
          </div>
          <div className="studio__muted">有效期至 {new Date(quote.expires_at).toLocaleString()}</div>
          <button className="studio__btn studio__btn--primary studio__btn--sm" onClick={confirmClone} disabled={busy}>
            {busy ? '提交中…' : '确认费用并开始复刻'}
          </button>
        </div>
      )}
    </div>
  )
}

/** 个人资产 tab（P2-3）：我的个人资产库，支持新建与复制进当前项目。 */
function PersonalAssetPanel({ projectId, onCopied }: { projectId: string; onCopied: () => void }) {
  const [assets, setAssets] = useState<SeriesAsset[] | null>(null)
  const [creating, setCreating] = useState(false)
  const [copyingId, setCopyingId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setAssets(await seriesApi.listPersonalAssets())
    } catch (e) {
      toast(e instanceof Error ? e.message : '个人资产加载失败', 'error')
      setAssets([])
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const create = async (input: { type: AssetType; name: string; description: string; tags: string[] }) => {
    setCreating(true)
    try {
      await seriesApi.createPersonalAsset(input)
      toast('个人资产已创建', 'success')
      void load()
    } catch (e) {
      toast(e instanceof Error ? e.message : '创建失败', 'error')
    } finally {
      setCreating(false)
    }
  }

  const copyToProject = async (asset: SeriesAsset) => {
    if (copyingId) return
    setCopyingId(asset.id)
    try {
      await seriesApi.copyPersonalAssetToProject(asset.id, projectId)
      toast(`「${asset.name}」已复制到项目`, 'success')
      onCopied()
    } catch (e) {
      toast(e instanceof Error ? e.message : '复制失败', 'error')
    } finally {
      setCopyingId(null)
    }
  }

  return (
    <section>
      <AssetCreateForm onCreate={create} creating={creating} />
      {!assets ? (
        <div className="studio__muted">加载中…</div>
      ) : assets.length === 0 ? (
        <div className="studio__muted">暂无个人资产。个人资产不属于任何项目，可复用到多个短剧项目。</div>
      ) : (
        <AssetGroups
          assets={assets}
          action={(a) => (
            <button className="studio__btn studio__btn--sm" onClick={() => copyToProject(a)} disabled={copyingId === a.id}>
              {copyingId === a.id ? '复制中…' : '复制到项目'}
            </button>
          )}
        />
      )}
    </section>
  )
}

export default function AssetPanel({
  projectId,
  assets,
  jobs,
  onChanged,
}: {
  projectId: string
  assets: SeriesAsset[]
  jobs: Job[]
  onChanged: () => void
}) {
  const [tab, setTab] = useState<'project' | 'personal'>('project')
  const [creating, setCreating] = useState(false)

  const create = async (input: { type: AssetType; name: string; description: string; tags: string[] }) => {
    setCreating(true)
    try {
      await seriesApi.createAsset(projectId, input)
      toast('资产已创建', 'success')
      onChanged()
    } catch (e) {
      toast(e instanceof Error ? e.message : '创建失败', 'error')
    } finally {
      setCreating(false)
    }
  }

  return (
    <section>
      <div className="asset-tabs">
        <button
          className={`studio__btn studio__btn--sm${tab === 'project' ? ' studio__btn--primary' : ''}`}
          onClick={() => setTab('project')}
        >
          项目资产
        </button>
        <button
          className={`studio__btn studio__btn--sm${tab === 'personal' ? ' studio__btn--primary' : ''}`}
          onClick={() => setTab('personal')}
        >
          个人资产
        </button>
      </div>

      {tab === 'project' ? (
        <>
          <AssetCreateForm onCreate={create} creating={creating} />
          <AssetGroups
            assets={assets}
            action={(asset) =>
              asset.type === 'voice' && asset.status !== 'retired' ? (
                <VoiceCloneControls projectId={projectId} asset={asset} jobs={jobs} onChanged={onChanged} />
              ) : null
            }
          />
          {assets.length === 0 && <div className="studio__muted">暂无资产，先用上方表单创建角色/风格/场景/音色。</div>}
        </>
      ) : (
        <PersonalAssetPanel projectId={projectId} onCopied={onChanged} />
      )}
    </section>
  )
}
