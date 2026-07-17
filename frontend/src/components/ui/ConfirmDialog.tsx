import { useEffect, useState } from 'react'
import './ConfirmDialog.css'

export interface ConfirmOptions {
  title: string
  description?: string
  confirmText?: string
  danger?: boolean
}

interface Pending extends ConfirmOptions {
  resolve: (ok: boolean) => void
}

let pending: Pending | null = null
const listeners = new Set<() => void>()

function notify() {
  listeners.forEach((l) => l())
}

/** 确认弹窗：if (await confirmDialog({ title: '删除该项目？', danger: true })) ... */
export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    pending?.resolve(false) // 理论上不会并发，兜底关闭上一个
    pending = { ...opts, resolve }
    notify()
  })
}

export function ConfirmHost() {
  const [, force] = useState(0)

  useEffect(() => {
    const rerender = () => force((n) => n + 1)
    listeners.add(rerender)
    return () => {
      listeners.delete(rerender)
    }
  }, [])

  if (!pending) return null

  const close = (ok: boolean) => {
    const p = pending
    pending = null
    notify()
    p?.resolve(ok)
  }

  return (
    <div className="confirm-overlay" onClick={() => close(false)}>
      <div className="confirm" onClick={(e) => e.stopPropagation()}>
        <div className="confirm__title">{pending.title}</div>
        {pending.description && <div className="confirm__desc">{pending.description}</div>}
        <div className="confirm__actions">
          <button className="confirm__btn confirm__btn--ghost" onClick={() => close(false)}>
            取消
          </button>
          <button
            className={`confirm__btn ${pending.danger ? 'confirm__btn--danger' : 'confirm__btn--primary'}`}
            onClick={() => close(true)}
            autoFocus
          >
            {pending.confirmText ?? '确定'}
          </button>
        </div>
      </div>
    </div>
  )
}
