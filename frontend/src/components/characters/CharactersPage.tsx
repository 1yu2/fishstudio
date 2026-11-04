import { useEffect, useMemo, useRef, useState } from 'react'
import { Plus } from 'lucide-react'
import { useOutletContext, useParams } from 'react-router-dom'
import { libraryApi } from '../../api/library'
import type { ProjectOutletContext } from '../project/outletContext'
import { toast } from '../ui/Toast'
import CharacterCard from './CharacterCard'
import { CharacterFormDialog } from './CharacterDialogs'
import LibraryPanel from './LibraryPanel'
import { buildCharacters, newCharacterNode, type Character } from './characters'
import { useCharacters } from './useCharacters'
import './characters.css'

export { characterGate } from './characters'

/** 第 2 步：角色页。角色就是画布里的角色图片节点（读写画布 graph 与 runs，不新建任何表）。 */
export default function CharactersPage() {
  const { canvasId = '' } = useParams()
  const outlet = useOutletContext<ProjectOutletContext | null>()
  const refreshSummary = outlet?.refreshSummary
  const data = useCharacters(canvasId)
  const characters = useMemo(() => buildCharacters(data.nodes, data.edges, data.runs), [data.nodes, data.edges, data.runs])
  const [creating, setCreating] = useState(false)
  const [savingName, setSavingName] = useState<string | null>(null)
  const [libraryVersion, setLibraryVersion] = useState(0)

  // 保存完成或结果变化后，让项目页的步骤条 / 底栏尽快更新，不等轮询
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; return }
    refreshSummary?.()
  }, [data.revision, data.signature, refreshSummary])

  const create = (value: { name: string; description: string }) => {
    data.update((current) => ({ ...current, nodes: [...current.nodes, newCharacterNode(current.nodes, current.edges, value.name, value.description)] }))
  }

  const saveToLibrary = async (character: Character) => {
    if (savingName) return
    setSavingName(character.name)
    try {
      await data.flushThen()
      await libraryApi.saveToLibrary(canvasId, character.name)
      toast('已存入角色库', 'success')
      setLibraryVersion((value) => value + 1)
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : '存入角色库失败', 'error')
    } finally { setSavingName(null) }
  }

  if (data.error) return <div className="ch-message" role="alert">{data.error}</div>
  if (!data.loaded) return <div className="ch-message" aria-busy="true">角色加载中…</div>
  return (
    <div className="ch">
      <section className="ch-list" aria-label="本集角色">
        {data.locked && <div className="ch-busy" role="status">正在加入角色，请稍候…</div>}
        <fieldset className="ch-lock" disabled={data.locked} aria-busy={data.locked}>
        <div className="ch-list__head">
          <div>
            <h2>本集角色 · {characters.length}</h2>
            <span className="ch-muted">定稿后会用于所有镜头，保证长相一致</span>
          </div>
          {characters.length > 0 && (
            <button type="button" className="ch-btn" onClick={() => setCreating(true)}><Plus size={13} aria-hidden="true" />新建角色</button>
          )}
        </div>
        {characters.length === 0 ? (
          <div className="ch-empty">
            <h3>还没有角色</h3>
            <p>角色通常在剧本确认后自动识别；也可以手动添加，或从右侧角色库加入。</p>
            <div><button type="button" className="ch-btn ch-btn--primary" onClick={() => setCreating(true)}>新建角色</button></div>
          </div>
        ) : characters.map((character) => (
          <CharacterCard key={character.name} character={character} data={data} saving={savingName === character.name} onSaveToLibrary={(item) => void saveToLibrary(item)} />
        ))}
        </fieldset>
      </section>
      <LibraryPanel data={data} version={libraryVersion} />
      {creating && (
        <CharacterFormDialog
          title="新建角色" initial={{ name: '', description: '' }} submitText="新建" nodes={data.nodes} edges={data.edges}
          onSubmit={create} onClose={() => setCreating(false)}
        />
      )}
    </div>
  )
}
