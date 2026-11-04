import { useEffect, useState } from 'react'
import { Loader2, Plus } from 'lucide-react'
import { ProtectedImage } from '../common/ProtectedMedia'
import { newRequestId, resolveInputs, statusText } from '../nodeCanvas/graph'
import { priceSuffix } from '../storyboard/shots'
import { CharacterFormDialog, LookDialog } from './CharacterDialogs'
import { CANDIDATE_COUNT, editCharacter, splitCandidates, type Character } from './characters'
import type { CharactersData } from './useCharacters'
import { useQuote } from './useQuote'

const CHIP: Record<string, string> = { final: '已定稿', pick: '等你挑选', running: '生成中', empty: '待生成', failed: '生成失败' }

/** 一个角色卡：随当前造型的状态显示定稿图 / 候选图 / 进度 / 生成入口 / 失败原因。 */
export default function CharacterCard({ character, data, saving, onSaveToLibrary }: {
  character: Character
  data: CharactersData
  saving: boolean
  onSaveToLibrary: (character: Character) => void
}) {
  const [lookId, setLookId] = useState(character.base.node.id)
  const [repick, setRepick] = useState(false)
  const [chosen, setChosen] = useState<string | null>(null)
  const [showEarlier, setShowEarlier] = useState(false)
  const [busy, setBusy] = useState(false)
  const [dialog, setDialog] = useState<'edit' | 'look' | null>(null)

  const view = character.looks.find((look) => look.node.id === lookId) ?? character.base
  useEffect(() => { setChosen(null); setRepick(false); setShowEarlier(false) }, [view.node.id])

  const resolved = resolveInputs(view.node, data.nodes, data.edges, data.runs, data.aspectRatio)
  // 每次都出 4 张候选，不跟随节点上的 count（旧画布里角色节点的 count 是 1）
  const request = { ...resolved.request, count: CANDIDATE_COUNT }
  const model = String((view.node.data as { model?: string }).model || '')
  const problem = !model && data.models?.image_problem ? data.models.image_problem : ''
  const blockers = [...(problem ? [problem] : []), ...resolved.blockers]
  const mode = view.state === 'running' ? 'running' : view.state === 'failed' ? 'failed'
    : view.state === 'pick' || (repick && view.state === 'final') ? 'pick' : view.state
  const wantsPrice = mode === 'empty' || mode === 'pick' || mode === 'failed'
  const { price, error, loading } = useQuote(data.quote, request, wantsPrice && blockers.length === 0)
  const reasons = [...blockers, ...(error ? [error] : [])]
  const { latest, earlier } = splitCandidates(view.results)

  const generate = async () => {
    setBusy(true)
    try { await data.run(view.node.id, { ...request, client_request_id: newRequestId() }) } finally { setBusy(false) }
  }
  const finalize = () => {
    if (!chosen) return
    data.updateData(view.node.id, { selected: chosen })
    setRepick(false); setChosen(null)
  }
  const generateLabel = mode === 'empty' ? `生成 ${CANDIDATE_COUNT} 张` : mode === 'failed' ? '重试' : `再生成 ${CANDIDATE_COUNT} 张`
  // 付费按钮只在拿到与当前请求一致的成功报价后才可点
  const priceText = loading ? ' · 报价中…' : priceSuffix(price)
  const canGenerate = !busy && reasons.length === 0 && !!price
  const generateButton = (
    <button type="button" className="ch-btn" disabled={!canGenerate} title={reasons[0]} onClick={() => void generate()}>
      {busy && <Loader2 size={12} className="ch-spin" aria-hidden="true" />}
      {generateLabel}{priceText}
    </button>
  )
  const tile = (result: (typeof view.results)[number], index: number) => (
    <button
      key={result.artifact_id} type="button" className={`ch-tile${chosen === result.artifact_id ? ' ch-tile--on' : ''}`}
      aria-label={`选择第 ${index + 1} 张`} aria-pressed={chosen === result.artifact_id} onClick={() => setChosen(result.artifact_id)}
    >
      <ProtectedImage src={result.url} alt="" />
    </button>
  )

  return (
    <article className={`ch-card${mode === 'pick' ? ' ch-card--pick' : ''}`} aria-label={`角色 ${character.name}`}>
      <header className="ch-card__head">
        <span className="ch-card__name">{character.name}</span>
        {!view.isBase && <span className="ch-chip">{view.name}</span>}
        <span className={`ch-chip ch-chip--${mode}`}>{CHIP[mode]}</span>
        {character.libraryAssetId && <span className="ch-chip">来自角色库</span>}
      </header>
      <div className="ch-card__body">
        <div className="ch-main">
          {mode === 'final' && view.final && (
            <div className="ch-final"><ProtectedImage src={view.final.url} alt={`${character.name}定稿图`} /></div>
          )}
          {mode === 'running' && (
            <div className="ch-running" role="status">
              <Loader2 size={18} className="ch-spin" aria-hidden="true" />
              <div>
                <div>正在生成 {CANDIDATE_COUNT} 张设定图</div>
                <div className="ch-muted">{view.job ? statusText(view.job) : '生成中'}</div>
                <div className="ch-bar" aria-hidden="true"><span style={{ width: `${view.progress}%` }} /></div>
              </div>
            </div>
          )}
          {mode === 'pick' && (
            <>
              <div className="ch-grid" role="group" aria-label="候选图">{latest.map(tile)}</div>
              {earlier.length > 0 && (
                <>
                  <button type="button" className="ch-link" aria-expanded={showEarlier} onClick={() => setShowEarlier((value) => !value)}>
                    {showEarlier ? '收起更早的' : `更早的 ${earlier.length} 张`}
                  </button>
                  {showEarlier && <div className="ch-grid" role="group" aria-label="更早的候选图">{earlier.map((result, index) => tile(result, latest.length + index))}</div>}
                </>
              )}
            </>
          )}
          {mode === 'failed' && <div className="ch-error" role="alert">生成失败：{view.job?.error || '请重试'}</div>}
          <p className="ch-desc">{String((view.node.data as { prompt?: string }).prompt || '') || '还没有外貌描述'}</p>
          <div className="ch-looks">
            <span className="ch-muted">造型</span>
            <div className="ch-looks__row">
              {character.looks.map((look) => (
                <button
                  key={look.node.id} type="button" className={`ch-look${look.node.id === view.node.id ? ' ch-look--on' : ''}`}
                  aria-label={`造型 ${look.name}`} aria-pressed={look.node.id === view.node.id} title={look.name} onClick={() => setLookId(look.node.id)}
                >
                  {(look.final || look.results[0]) ? <ProtectedImage src={(look.final || look.results[0]).url} alt="" /> : <span>{look.name}</span>}
                </button>
              ))}
              <button type="button" className="ch-look ch-look--add" aria-label="新增造型" onClick={() => setDialog('look')}><Plus size={14} aria-hidden="true" /></button>
            </div>
          </div>
        </div>
        <div className="ch-actions">
          {mode === 'pick' && (
            <>
              <button type="button" className="ch-btn ch-btn--primary" disabled={!chosen} onClick={finalize}>定为标准形象</button>
              {generateButton}
              {repick && <button type="button" className="ch-btn" onClick={() => { setRepick(false); setChosen(null) }}>先不换</button>}
            </>
          )}
          {(mode === 'empty' || mode === 'failed') && <button type="button" className="ch-btn ch-btn--primary" disabled={!canGenerate} title={reasons[0]} onClick={() => void generate()}>
            {busy && <Loader2 size={12} className="ch-spin" aria-hidden="true" />}{generateLabel}{priceText}
          </button>}
          {mode === 'final' && <button type="button" className="ch-btn" onClick={() => setRepick(true)}>重新挑选</button>}
          <button type="button" className="ch-btn" onClick={() => setDialog('edit')}>编辑</button>
          <button type="button" className="ch-btn" disabled={!character.finalized || saving} title={character.finalized ? undefined : '先给角色定稿'} onClick={() => onSaveToLibrary(character)}>
            {saving ? '保存中…' : '存入角色库'}
          </button>
        </div>
      </div>
      {reasons.length > 0 && wantsPrice && <ul className="ch-reasons">{reasons.map((text) => <li key={text}>{text}</li>)}</ul>}
      {dialog === 'edit' && (
        <CharacterFormDialog
          title="编辑角色" initial={{ name: character.name, description: character.description }} submitText="保存"
          nodes={data.nodes} edges={data.edges} except={character.name} onClose={() => setDialog(null)}
          onSubmit={(value) => data.update((current) => ({ ...current, nodes: editCharacter(current.nodes, character, value) }))}
        />
      )}
      {dialog === 'look' && (
        <LookDialog
          character={character} data={data} onClose={() => setDialog(null)}
          onCreate={async (node, generate) => {
            data.update((current) => ({ ...current, nodes: [...current.nodes, node] }))
            setLookId(node.id)
            if (generate) {
              const next = resolveInputs(node, [...data.nodes, node], data.edges, data.runs, data.aspectRatio).request
              await data.run(node.id, { ...next, count: CANDIDATE_COUNT, client_request_id: newRequestId() })
            }
          }}
        />
      )}
    </article>
  )
}
