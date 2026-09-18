import { useEffect, useState } from 'react'
import { toast } from '../ui/Toast'
import * as seriesApi from '../../api/series'
import type { BibleKind, BibleRevision } from '../../api/series'
import './studio.css'

const DEFAULTS: Record<BibleKind, Record<string, unknown>> = {
  series: {
    schema_version: 1,
    premise: '',
    theme: '',
    genre: '',
    audience: '',
    world_rules: [],
    main_conflict: '',
    characters: [],
    relationships: [],
    season_arc: [],
    forbidden_content: [],
    continuity_rules: [],
  },
  style: {
    schema_version: 1,
    visual_style: '',
    color_palette: [],
    lighting: '',
    composition: [],
    materials: [],
    aspect_ratio: '9:16',
    platform: 'xiaohongshu',
    negative_constraints: [],
  },
}

const LABELS: Record<BibleKind, string> = { series: 'Series Bible', style: 'Style Bible' }

function BibleEditor({
  projectId,
  kind,
  onChanged,
}: {
  projectId: string
  kind: BibleKind
  onChanged: () => void
}) {
  const [revisions, setRevisions] = useState<BibleRevision[]>([])
  const [text, setText] = useState(JSON.stringify(DEFAULTS[kind], null, 2))
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const current = revisions[0]

  const load = async () => {
    try {
      setRevisions(await seriesApi.listBibleRevisions(projectId, kind))
    } catch (error) {
      toast(error instanceof Error ? error.message : `${LABELS[kind]} 加载失败`, 'error')
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, kind])

  const startEdit = () => {
    setText(JSON.stringify(current?.structured_payload || DEFAULTS[kind], null, 2))
    setEditing(true)
  }

  const save = async () => {
    let payload: Record<string, unknown>
    try {
      const parsed = JSON.parse(text) as unknown
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('必须是 JSON 对象')
      payload = parsed as Record<string, unknown>
    } catch (error) {
      toast(error instanceof Error ? `JSON 无效：${error.message}` : 'JSON 无效', 'error')
      return
    }
    setBusy(true)
    try {
      await seriesApi.createBibleRevision(projectId, kind, {
        structured_payload: payload,
        source_type: 'user',
        based_on_revision: current?.revision,
      })
      toast(`${LABELS[kind]} 已保存为新版本`, 'success')
      setEditing(false)
      await load()
      onChanged()
    } catch (error) {
      toast(error instanceof Error ? error.message : '保存失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  const approve = async () => {
    if (!current) return
    setBusy(true)
    try {
      await seriesApi.submitApproval({
        project_id: projectId,
        target_type: kind === 'series' ? 'series_bible' : 'style_bible',
        target_id: projectId,
        target_revision: current.revision,
        action: 'approve',
      })
      toast(`${LABELS[kind]} 已批准`, 'success')
      await load()
      onChanged()
    } catch (error) {
      toast(error instanceof Error ? error.message : '审批失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="studio__card bible-card">
      <div className="sb-shot__head">
        <b>{LABELS[kind]}</b>
        {current && <span className={`studio__badge studio__badge--${current.status}`}>rev {current.revision} · {current.status}</span>}
        <span style={{ flex: 1 }} />
        <button className="studio__btn studio__btn--sm" disabled={busy} onClick={startEdit}>
          {current ? '基于当前版修订' : '创建'}
        </button>
      </div>
      {editing ? (
        <>
          <textarea className="studio__textarea script-editor__json" rows={22} value={text} onChange={(event) => setText(event.target.value)} />
          <div className="script-editor__actions">
            <button className="studio__btn studio__btn--primary" disabled={busy} onClick={save}>保存新版本</button>
            <button className="studio__btn" disabled={busy} onClick={() => setEditing(false)}>取消</button>
          </div>
        </>
      ) : current ? (
        <>
          <pre className="bible-card__payload">{JSON.stringify(current.structured_payload, null, 2)}</pre>
          {current.status === 'draft' && (
            <button className="studio__btn studio__btn--primary studio__btn--sm" disabled={busy} onClick={approve}>审批通过</button>
          )}
          {revisions.length > 1 && (
            <div className="studio__muted" style={{ marginTop: 10 }}>
              历史版本：{revisions.slice(1).map((item) => `rev ${item.revision} (${item.status})`).join('、')}
            </div>
          )}
        </>
      ) : (
        <div className="studio__muted">尚未创建。批准后才允许新版 Workflow 进入单集剧本。</div>
      )}
    </section>
  )
}

export default function BiblePanel({ projectId, onChanged }: { projectId: string; onChanged: () => void }) {
  return (
    <div className="bible-grid">
      <BibleEditor projectId={projectId} kind="series" onChanged={onChanged} />
      <BibleEditor projectId={projectId} kind="style" onChanged={onChanged} />
    </div>
  )
}
