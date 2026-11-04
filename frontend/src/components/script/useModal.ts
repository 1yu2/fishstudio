import { useEffect, useRef } from 'react'

const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * 对话框 / 抽屉的焦点管理：打开时把焦点移进来，Tab / Shift+Tab 在内部循环，Escape 关闭，关闭后焦点还给触发它的元素。
 * 返回要挂到容器上的 ref 与 onKeyDown。
 */
export function useModal<T extends HTMLElement>(onClose: () => void) {
  const ref = useRef<T>(null)
  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose }, [onClose])

  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null
    const container = ref.current
    if (container) {
      const first = container.querySelector<HTMLElement>(FOCUSABLE)
      if (first) first.focus()
      else { container.tabIndex = -1; container.focus() }
    }
    return () => { if (trigger && document.contains(trigger)) trigger.focus() }
  }, [])

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') { event.stopPropagation(); closeRef.current(); return }
    if (event.key !== 'Tab' || !ref.current) return
    const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
    if (items.length === 0) { event.preventDefault(); return }
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    if (event.shiftKey && (active === first || !ref.current.contains(active))) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && (active === last || !ref.current.contains(active))) { event.preventDefault(); first.focus() }
  }
  return { ref, onKeyDown }
}
