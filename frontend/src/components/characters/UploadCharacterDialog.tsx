import { useState } from 'react'
import { libraryApi, type LibraryCharacter } from '../../api/library'
import Modal from '../ui/Modal'

const MAX_BYTES = 10 * 1024 * 1024
const TYPES = ['image/png', 'image/jpeg', 'image/webp']

/** 上传自己的角色图到角色库：名字、描述、图片（PNG / JPEG / WebP，≤ 10MB）。 */
export default function UploadCharacterDialog({ onClose, onDone }: { onClose: () => void; onDone: (item: LibraryCharacter) => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const pick = (next: File | null) => {
    setFile(next)
    if (!next) { setError(''); return }
    if (!TYPES.includes(next.type)) setError('只支持 PNG / JPEG / WebP 图片')
    else if (next.size > MAX_BYTES) setError('图片不能超过 10MB')
    else setError('')
  }
  const invalid = !name.trim() || !file || !TYPES.includes(file.type) || file.size > MAX_BYTES
  const submit = async () => {
    if (invalid || busy || !file) return
    setBusy(true); setError('')
    try {
      onDone(await libraryApi.upload({ name: name.trim(), description: description.trim(), file }))
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '上传失败')
      setBusy(false)
    }
  }
  return (
    <Modal title="上传自己的角色图" onClose={onClose}>
      <label className="modal__field">角色名
        <input type="text" value={name} maxLength={60} onChange={(event) => setName(event.target.value)} />
      </label>
      <label className="modal__field">外貌描述（选填）
        <textarea rows={3} value={description} maxLength={500} onChange={(event) => setDescription(event.target.value)} />
      </label>
      <label className="modal__field">角色图
        <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => pick(event.target.files?.[0] ?? null)} />
      </label>
      {error && <div className="modal__error" role="alert">{error}</div>}
      <div className="modal__actions">
        <button type="button" className="modal__btn" onClick={onClose}>取消</button>
        <button type="button" className="modal__btn modal__btn--primary" disabled={invalid || busy} onClick={() => void submit()}>{busy ? '上传中…' : '上传'}</button>
      </div>
    </Modal>
  )
}
