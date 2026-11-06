import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { X } from 'lucide-react'
import './Modal.css'

const FOCUSABLE = 'input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'

// 多个弹窗叠加时只在第一个打开时锁、最后一个关闭时还原
let scrollLocks = 0
let saved: Array<[HTMLElement, string]> = []

/** 页面实际滚动的是项目框架里的 .pf-body，不是 body：两个都锁，找不到 .pf-body 就只锁 body */
function lockScroll() {
  const targets = [document.body, document.querySelector<HTMLElement>('.pf-body')].filter((el): el is HTMLElement => !!el)
  saved = targets.map((el) => [el, el.style.overflow])
  for (const el of targets) el.style.overflow = 'hidden'
}
function unlockScroll() {
  for (const [el, value] of saved) el.style.overflow = value
  saved = []
}

interface Props {
  title: string
  onClose: () => void
  children: ReactNode
  /** dialog：居中对话框（默认）；drawer：靠右的整高抽屉 */
  variant?: 'dialog' | 'drawer'
  /** 给了就在标题右侧放一个关闭按钮（aria-label 取这个值） */
  closeLabel?: string
  /** 追加到弹窗框上的 class，用来调宽度等 */
  className?: string
}

/**
 * 通用对话框 / 抽屉：点遮罩 / Esc 关闭；打开时焦点落到第一个可聚焦元素（没有就落到弹窗本身），
 * Tab / Shift+Tab 只在弹窗内循环，关闭后焦点回到打开它的元素；打开期间锁定页面滚动。
 */
export default function Modal({ title, onClose, children, variant = 'dialog', closeLabel, className }: Props) {
  const box = useRef<HTMLDivElement>(null)
  // onClose 每次渲染都可能是新函数：放进 ref，避免重复聚焦打断输入
  const close = useRef(onClose)
  useEffect(() => { close.current = onClose })
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') close.current() }
    document.addEventListener('keydown', onKey)
    const first = box.current?.querySelector<HTMLElement>(FOCUSABLE)
    if (first) first.focus()
    else if (box.current) { box.current.tabIndex = -1; box.current.focus() }
    if (scrollLocks === 0) lockScroll()
    scrollLocks += 1
    return () => {
      document.removeEventListener('keydown', onKey)
      scrollLocks -= 1
      if (scrollLocks === 0) unlockScroll()
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

  const drawer = variant === 'drawer'
  return (
    <div className={`modal-overlay${drawer ? ' modal-overlay--drawer' : ''}`} onClick={onClose}>
      <div ref={box} className={`modal${drawer ? ' modal--drawer' : ''}${className ? ` ${className}` : ''}`} role="dialog" aria-modal="true" aria-label={title}
        onClick={(event) => event.stopPropagation()} onKeyDown={trap}>
        {closeLabel ? (
          <header className="modal__head">
            <h2 className="modal__title">{title}</h2>
            <button type="button" className="modal__close" aria-label={closeLabel} onClick={onClose}><X size={14} aria-hidden="true" /></button>
          </header>
        ) : <h2 className="modal__title">{title}</h2>}
        {children}
      </div>
    </div>
  )
}
