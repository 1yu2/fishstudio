import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import type { StoredNode } from '../../api/nodeCanvas'
import { resolveInputs } from '../nodeCanvas/graph'
import Modal from '../ui/Modal'
import { priceSuffix } from '../storyboard/shots'
import { CANDIDATE_COUNT, DEFAULT_LOOK, nameTaken, newLookNode, type Character } from './characters'
import { useQuote } from './useQuote'
import type { CharactersData } from './useCharacters'

/** 新建角色 / 编辑角色共用：名字 + 外貌描述。 */
export function CharacterFormDialog({ title, initial, submitText, nodes, edges, except, onSubmit, onClose }: {
  title: string
  initial: { name: string; description: string }
  submitText: string
  nodes: StoredNode[]
  edges: CharactersData['edges']
  /** 改名时当前角色自己的名字（不算重名） */
  except?: string
  onSubmit: (value: { name: string; description: string }) => void
  onClose: () => void
}) {
  const [name, setName] = useState(initial.name)
  const [description, setDescription] = useState(initial.description)
  const taken = !!name.trim() && nameTaken(nodes, edges, name, except)
  const submit = () => { if (name.trim() && !taken) { onSubmit({ name: name.trim(), description: description.trim() }); onClose() } }
  return (
    <Modal title={title} onClose={onClose}>
      <label className="modal__field">角色名
        <input type="text" value={name} maxLength={60} onChange={(event) => setName(event.target.value)} />
      </label>
      <label className="modal__field">外貌描述
        <textarea rows={4} value={description} maxLength={1000} onChange={(event) => setDescription(event.target.value)} placeholder="例如：白发及腰，金色竖瞳，白色交领长裙" />
      </label>
      {taken && <div className="modal__error" role="alert">已有同名角色</div>}
      <div className="modal__actions">
        <button type="button" className="modal__btn" onClick={onClose}>取消</button>
        <button type="button" className="modal__btn modal__btn--primary" disabled={!name.trim() || taken} onClick={submit}>{submitText}</button>
      </div>
    </Modal>
  )
}

/** 新造型：造型名 + 与基础造型的差异；可以只建好，也可以建好后立即生成 4 张（先看报价）。 */
export function LookDialog({ character, data, onCreate, onClose }: {
  character: Character
  data: CharactersData
  onCreate: (node: StoredNode, generate: boolean) => Promise<void>
  onClose: () => void
}) {
  const [look, setLook] = useState('')
  const [difference, setDifference] = useState('')
  const [busy, setBusy] = useState(false)
  const name = look.trim()
  const reserved = name === DEFAULT_LOOK
  const duplicate = !!name && !reserved && character.looks.some((item) => item.name === name)
  const preview = newLookNode(data.nodes, data.edges, character, look || '新造型', difference)
  const resolved = resolveInputs(preview, data.nodes, data.edges, data.runs, data.aspectRatio)
  const request = { ...resolved.request, count: CANDIDATE_COUNT }
  const model = String((preview.data as { model?: string }).model || '')
  const problem = !model && data.models?.image_problem ? data.models.image_problem : ''
  const blockers = [...(problem ? [problem] : []), ...resolved.blockers]
  const { price, error, loading } = useQuote(data.quote, request, !!name && !duplicate && !reserved && blockers.length === 0)
  const invalid = !name || duplicate || reserved
  const create = async (generate: boolean) => {
    if (invalid || busy) return
    setBusy(true)
    try { await onCreate(newLookNode(data.nodes, data.edges, character, name, difference), generate); onClose() }
    finally { setBusy(false) }
  }
  return (
    <Modal title={`${character.name} · 新造型`} onClose={onClose}>
      <label className="modal__field">造型名
        <input type="text" value={look} maxLength={30} placeholder="例如：冬装" onChange={(event) => setLook(event.target.value)} />
      </label>
      <label className="modal__field">和基础造型的差异
        <textarea rows={3} value={difference} maxLength={500} placeholder="例如：披黑色斗篷，头发束起" onChange={(event) => setDifference(event.target.value)} />
      </label>
      <p className="modal__hint">描述会写成“基础描述，造型：差异”。</p>
      {duplicate && <div className="modal__error" role="alert">这个角色已有同名造型</div>}
      {reserved && <div className="modal__error" role="alert">“默认”是基础造型，请换个名字</div>}
      {!invalid && (blockers.length > 0 || error) && <div className="modal__error" role="alert">{[...blockers, error].filter(Boolean)[0]}</div>}
      <div className="modal__actions">
        <button type="button" className="modal__btn" onClick={onClose}>取消</button>
        <button type="button" className="modal__btn" disabled={invalid || busy} onClick={() => void create(false)}>只建好</button>
        <button type="button" className="modal__btn modal__btn--primary" disabled={invalid || busy || blockers.length > 0 || !!error || !price} onClick={() => void create(true)}>
          {busy && <Loader2 size={12} className="ch-spin" aria-hidden="true" />}
          建好并生成{loading ? ' · 报价中…' : priceSuffix(price)}
        </button>
      </div>
    </Modal>
  )
}
