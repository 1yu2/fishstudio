import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { scriptApi, type ScriptPayload, type ScriptRevisionBrief } from '../../api/script'
import Modal from '../ui/Modal'
import { toast } from '../ui/Toast'
import { SOURCE_LABEL, flattenScript } from './model'

function when(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}

interface Props {
  canvasId: string
  revisions: ScriptRevisionBrief[]
  current: number | null
  onRestore: (revision: number) => Promise<void>
  onClose: () => void
}

/** 历史版本抽屉：列出版本，点击预览只读内容，可恢复某一版（恢复会新建一版，不覆盖历史）。 */
export default function HistoryDrawer({ canvasId, revisions, current, onRestore, onClose }: Props) {
  const [preview, setPreview] = useState<{ revision: number; payload: ScriptPayload } | null>(null)
  const [loading, setLoading] = useState<number | null>(null)
  const [restoring, setRestoring] = useState(false)

  const open = async (revision: number) => {
    setLoading(revision)
    try {
      const value = await scriptApi.revision(canvasId, revision)
      setPreview({ revision, payload: value.payload })
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : '版本加载失败', 'error')
    } finally { setLoading(null) }
  }

  const restore = async () => {
    if (!preview) return
    setRestoring(true)
    try {
      await onRestore(preview.revision)
      toast(`已恢复 v${preview.revision}，作为新版本保存`, 'success')
      onClose()
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : '恢复失败', 'error')
    } finally { setRestoring(false) }
  }

  return (
    <Modal title="历史版本" variant="drawer" closeLabel="关闭历史版本" onClose={onClose}>
      {revisions.length === 0 ? <p className="sc-muted sc-drawer__pad">还没有保存过的版本</p> : (
        <ul className="sc-versions">
          {revisions.map((item) => (
            <li key={item.revision}>
              <button type="button" className={`sc-version${preview?.revision === item.revision ? ' sc-version--on' : ''}`} onClick={() => void open(item.revision)}>
                <span className="sc-version__no">v{item.revision}{item.revision === current ? ' · 当前' : ''}</span>
                <span>{SOURCE_LABEL[item.source] ?? item.source}</span>
                <span className="sc-muted">{item.word_count.toLocaleString('en-US')} 字 · {when(item.updated_at)}</span>
                {loading === item.revision && <Loader2 size={12} className="sc-spin" aria-hidden="true" />}
              </button>
            </li>
          ))}
        </ul>
      )}
      {preview && (
        <div className="sc-drawer__preview">
          <div className="sc-drawer__bar">
            <strong>预览 v{preview.revision}（只读）</strong>
            {preview.revision !== current && <button type="button" className="sc-btn sc-btn--primary" disabled={restoring} onClick={() => void restore()}>恢复这一版</button>}
          </div>
          <pre className="sc-preview" aria-label={`v${preview.revision} 内容`}>{flattenScript(preview.payload) || '（空）'}</pre>
        </div>
      )}
    </Modal>
  )
}
