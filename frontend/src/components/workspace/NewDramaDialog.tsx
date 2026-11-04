import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { nodeCanvasApi } from '../../api/nodeCanvas'
import { paths } from '../../routePaths'
import './NewDramaDialog.css'

const RATIOS = [
  { value: '9:16', label: '竖屏 9:16' },
  { value: '16:9', label: '横屏 16:9' },
  { value: '1:1', label: '方形 1:1' },
]

/** 新建漫剧：填故事想法、选画幅，创建节点画布后进入项目。 */
export default function NewDramaDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const [idea, setIdea] = useState('')
  const [ratio, setRatio] = useState('9:16')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const ideaRef = useRef<HTMLTextAreaElement>(null)

  // 父组件每次轮询都会重建 onClose，用 ref 读取，避免重复聚焦
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    ideaRef.current?.focus()
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const text = idea.trim()
      const canvas = await nodeCanvasApi.create({
        title: text ? '' : '未命名漫剧',
        aspect_ratio: ratio,
        idea: text,
      })
      navigate(paths.nodeCanvas(canvas.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建失败，请重试')
      setBusy(false)
    }
  }

  return (
    <div className="ndd__overlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form className="ndd" role="dialog" aria-modal="true" aria-labelledby="ndd-title" onSubmit={(e) => void submit(e)}>
        <h2 id="ndd-title">新建漫剧</h2>
        <label className="ndd__field">
          <span>故事想法</span>
          <textarea
            ref={ideaRef}
            value={idea}
            onChange={(event) => setIdea(event.target.value)}
            rows={5}
            maxLength={20000}
            placeholder="一句话或一段故事都可以，例如：雪夜里，书生遇见了一只会说话的狐妖……"
          />
        </label>
        <fieldset className="ndd__field">
          <legend>画幅</legend>
          <div className="ndd__ratios">
            {RATIOS.map((item) => (
              <label key={item.value} className={ratio === item.value ? 'is-active' : ''}>
                <input type="radio" name="ratio" value={item.value} checked={ratio === item.value} onChange={() => setRatio(item.value)} />
                {item.label}
              </label>
            ))}
          </div>
        </fieldset>
        {error && <p className="ndd__error" role="alert">{error}</p>}
        <div className="ndd__actions">
          <button type="button" className="ndd__ghost" onClick={onClose} disabled={busy}>取消</button>
          <button type="submit" className="ndd__primary" disabled={busy}>{busy ? '创建中…' : '创建'}</button>
        </div>
      </form>
    </div>
  )
}
