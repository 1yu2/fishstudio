import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { libraryApi, type LibraryCharacter } from '../../api/library'
import { ConflictError } from '../../api/nodeCanvas'
import { paths } from '../../routePaths'
import { ProtectedImage } from '../common/ProtectedMedia'
import { toast } from '../ui/Toast'
import UploadCharacterDialog from './UploadCharacterDialog'
import type { CharactersData } from './useCharacters'

/** 右侧“角色库”：搜索、把库里的角色加入本项目、上传自己的角色图。 */
export default function LibraryPanel({ data, version }: { data: CharactersData; version: number }) {
  const canvasId = data.canvasId
  const [items, setItems] = useState<LibraryCharacter[] | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)

  const load = useCallback(async () => {
    try { setItems(await libraryApi.list(canvasId)); setError('') }
    catch (reason) { setError(reason instanceof Error ? reason.message : '角色库加载失败') }
  }, [canvasId])
  useEffect(() => { void load() }, [load, version])

  const add = async (item: LibraryCharacter) => {
    if (adding) return
    setAdding(item.id)
    try {
      await data.withLock(async () => {
        const revision = await data.savedRevision()
        await libraryApi.addToProject(canvasId, item.id, revision)
        await data.reload()
      })
      toast(`已加入「${item.name}」`, 'success')
    } catch (reason) {
      if (reason instanceof ConflictError) {
        toast('画布在别处被修改，已刷新，请再试一次', 'error')
        await data.withLock(() => data.reload()).catch(() => undefined)
      } else toast(reason instanceof Error ? reason.message : '加入失败', 'error')
    } finally {
      setAdding(null)
      await load()
    }
  }

  const needle = query.trim().toLowerCase()
  const shown = (items || []).filter((item) => !needle || item.name.toLowerCase().includes(needle) || item.description.toLowerCase().includes(needle))
  return (
    <aside className="ch-lib" aria-label="角色库">
      <div className="ch-lib__head"><h2>角色库</h2><Link to={paths.library}>管理</Link></div>
      <label className="ch-field">搜索角色
        <input type="search" value={query} placeholder="名字、外貌" onChange={(event) => setQuery(event.target.value)} />
      </label>
      <span className="ch-muted">你在其他项目里做好的角色，加进来后长相保持一致</span>
      {error && <div className="ch-error" role="alert">{error}</div>}
      {!items && !error && <span className="ch-muted" aria-busy="true">角色库加载中…</span>}
      {items && items.length === 0 && <span className="ch-muted">角色库还是空的。在项目里定稿后点“存入角色库”，或上传自己的角色图。</span>}
      {items && items.length > 0 && shown.length === 0 && <span className="ch-muted">没有匹配的角色</span>}
      <ul className="ch-lib__list">
        {shown.map((item) => (
          <li key={item.id} className="ch-lib__item">
            <div className="ch-lib__cover">{item.cover_url ? <ProtectedImage src={item.cover_url} alt="" /> : null}</div>
            <div className="ch-lib__text">
              <span className="ch-lib__name">{item.name}</span>
              {item.in_project
                ? <span className="ch-done">已在本项目</span>
                : <span className="ch-muted">{item.looks.length} 套造型</span>}
            </div>
            {!item.in_project && (
              <button type="button" className="ch-btn ch-btn--sm" disabled={!!adding} aria-label={`加入 ${item.name}`} onClick={() => void add(item)}>
                {adding === item.id ? '加入中…' : '加入'}
              </button>
            )}
          </li>
        ))}
      </ul>
      <button type="button" className="ch-dashed" onClick={() => setUploading(true)}>上传自己的角色图</button>
      {uploading && <UploadCharacterDialog onClose={() => setUploading(false)} onDone={() => { toast('已上传到角色库', 'success'); void load() }} />}
    </aside>
  )
}
