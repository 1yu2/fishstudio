import { useCallback, useEffect, useState } from 'react'
import { Plus, Users } from 'lucide-react'
import { libraryApi, type LibraryCharacter } from '../../api/library'
import { ProtectedImage } from '../common/ProtectedMedia'
import UploadCharacterDialog from '../characters/UploadCharacterDialog'
import { confirmDialog } from '../ui/ConfirmDialog'
import Modal from '../ui/Modal'
import { toast } from '../ui/Toast'
import { relativeTime } from './format'
import './WorkspacePage.css'
import './LibraryPage.css'

/** 改名 / 改描述 */
function EditDialog({ item, onClose, onDone }: { item: LibraryCharacter; onClose: () => void; onDone: (value: LibraryCharacter) => void }) {
  const [name, setName] = useState(item.name)
  const [description, setDescription] = useState(item.description)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    if (!name.trim() || busy) return
    setBusy(true); setError('')
    try { onDone(await libraryApi.update(item.id, { name: name.trim(), description: description.trim() })); onClose() }
    catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败'); setBusy(false) }
  }
  return (
    <Modal title="编辑角色" onClose={onClose}>
      <label className="modal__field">角色名
        <input type="text" value={name} maxLength={60} onChange={(event) => setName(event.target.value)} />
      </label>
      <label className="modal__field">外貌描述
        <textarea rows={4} value={description} maxLength={1000} onChange={(event) => setDescription(event.target.value)} />
      </label>
      {error && <div className="modal__error" role="alert">{error}</div>}
      <div className="modal__actions">
        <button type="button" className="modal__btn" onClick={onClose}>取消</button>
        <button type="button" className="modal__btn modal__btn--primary" disabled={!name.trim() || busy} onClick={() => void submit()}>保存</button>
      </div>
    </Modal>
  )
}

/** 角色库：个人角色资产。项目里定稿后存进来，在其他项目直接加入使用。 */
export default function LibraryPage() {
  const [items, setItems] = useState<LibraryCharacter[] | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [uploading, setUploading] = useState(false)
  const [editing, setEditing] = useState<LibraryCharacter | null>(null)

  const load = useCallback(async () => {
    try { setItems(await libraryApi.list()); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : '角色库加载失败') }
  }, [])
  useEffect(() => { void load() }, [load])

  const remove = async (item: LibraryCharacter) => {
    if (!(await confirmDialog({ title: `删除角色「${item.name}」？`, description: '只会从角色库移除，已经用到项目里的图片不受影响。', confirmText: '删除', danger: true }))) return
    try {
      await libraryApi.remove(item.id)
      setItems((current) => current && current.filter((value) => value.id !== item.id))
      toast('已删除', 'success')
    } catch (reason) { toast(reason instanceof Error ? reason.message : '删除失败', 'error') }
  }

  const needle = query.trim().toLowerCase()
  const shown = (items || []).filter((item) => !needle || item.name.toLowerCase().includes(needle) || item.description.toLowerCase().includes(needle))
  return (
    <div className="ws">
      <header className="ws-head">
        <div>
          <h1>角色库</h1>
          <span className="ws-muted">在项目里定稿的角色可以存进来，在其他项目直接使用</span>
        </div>
        <div className="ws-head__actions">
          <input type="search" className="lib-search" aria-label="搜索角色" placeholder="搜索名字、外貌" value={query} onChange={(event) => setQuery(event.target.value)} />
          <button type="button" className="ws-primary" onClick={() => setUploading(true)}><Plus size={14} aria-hidden="true" />上传自己的角色图</button>
        </div>
      </header>
      {error && <div className="ws-state" role="alert"><p>{error}</p></div>}
      {!items && !error && <div className="ws-state" aria-busy="true"><p>角色库加载中…</p></div>}
      {items && items.length === 0 && (
        <div className="ws-state ws-state--empty">
          <Users size={28} aria-hidden="true" />
          <h2>角色库还是空的</h2>
          <p>在项目里定稿的角色可以存进角色库，在其他项目直接使用；也可以上传自己的角色图。</p>
        </div>
      )}
      {items && items.length > 0 && shown.length === 0 && <div className="ws-state"><p>没有匹配的角色</p></div>}
      <ul className="lib-grid">
        {shown.map((item) => (
          <li key={item.id} className="lib-card">
            <div className="lib-card__cover">{item.cover_url ? <ProtectedImage src={item.cover_url} alt={`${item.name}封面`} /> : null}</div>
            <div className="lib-card__body">
              <strong className="lib-card__name">{item.name}</strong>
              <span className="ws-muted">{item.looks.length} 套造型{item.updated_at ? ` · ${relativeTime(item.updated_at)}` : ''}</span>
              {item.description && <p className="lib-card__desc">{item.description}</p>}
            </div>
            <div className="lib-card__actions">
              <button type="button" className="ws-btn-sm" aria-label={`编辑 ${item.name}`} onClick={() => setEditing(item)}>编辑</button>
              <button type="button" className="ws-btn-sm" aria-label={`删除 ${item.name}`} onClick={() => void remove(item)}>删除</button>
            </div>
          </li>
        ))}
      </ul>
      {uploading && <UploadCharacterDialog onClose={() => setUploading(false)} onDone={() => { toast('已上传到角色库', 'success'); void load() }} />}
      {editing && <EditDialog item={editing} onClose={() => setEditing(null)} onDone={(value) => setItems((current) => current && current.map((item) => item.id === value.id ? value : item))} />}
    </div>
  )
}
