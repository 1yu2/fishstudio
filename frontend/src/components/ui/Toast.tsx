import { useEffect, useState } from 'react'
import './Toast.css'

type ToastType = 'success' | 'error' | 'info'
interface ToastItem {
  id: number
  message: string
  type: ToastType
}

let nextId = 1
const listeners = new Set<(t: ToastItem) => void>()

/** 全局轻提示：toast('已删除', 'success') */
export function toast(message: string, type: ToastType = 'info'): void {
  const item = { id: nextId++, message, type }
  listeners.forEach((l) => l(item))
}

export function Toaster() {
  const [items, setItems] = useState<ToastItem[]>([])

  useEffect(() => {
    const push = (item: ToastItem) => {
      setItems((prev) => [...prev, item])
      setTimeout(() => {
        setItems((prev) => prev.filter((t) => t.id !== item.id))
      }, 3000)
    }
    listeners.add(push)
    return () => {
      listeners.delete(push)
    }
  }, [])

  return (
    <div className="toast-stack">
      {items.map((t) => (
        <div key={t.id} className={`toast toast--${t.type}`}>
          <span className="toast__icon">
            {t.type === 'success' ? '✓' : t.type === 'error' ? '⚠' : 'ℹ'}
          </span>
          {t.message}
        </div>
      ))}
    </div>
  )
}
