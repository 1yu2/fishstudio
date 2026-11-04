import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import './Modal.css'

const FOCUSABLE = 'input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'

/**
 * 通用对话框：点遮罩 / Esc 关闭；打开时焦点落到第一个输入框，Tab / Shift+Tab 只在对话框内循环，
 * 关闭后焦点回到打开它的元素。
 */
export default function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null)
  // onClose 每次渲染都可能是新函数：放进 ref，避免重复聚焦打断输入
  const close = useRef(onClose)
  useEffect(() => { close.current = onClose })
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') close.current() }
    document.addEventListener('keydown', onKey)
    box.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus()
    return () => {
      document.removeEventListener('keydown', onKey)
      if (opener && opener.isConnected) opener.focus()
    }
  }, [])

  const trap = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Tab') return
    const items = Array.from(box.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])
    if (items.length === 0) { event.preventDefault(); return }
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    if (event.shiftKey && (active === first || !box.current?.contains(active))) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && (active === last || !box.current?.contains(active))) { event.preventDefault(); first.focus() }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div ref={box} className="modal" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()} onKeyDown={trap}>
        <h2 className="modal__title">{title}</h2>
        {children}
      </div>
    </div>
  )
}
