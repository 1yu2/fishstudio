import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import Modal from './Modal'

function Host() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>打开</button>
      {open && (
        <Modal title="测试对话框" onClose={() => setOpen(false)}>
          <input type="text" aria-label="名字" />
          <button type="button">取消</button>
          <button type="button">确定</button>
        </Modal>
      )}
    </>
  )
}

describe('Modal focus handling', () => {
  it('focuses the first field, cycles Tab / Shift+Tab inside, and restores focus on close', () => {
    render(<Host />)
    const opener = screen.getByRole('button', { name: '打开' })
    opener.focus()
    fireEvent.click(opener)
    const input = screen.getByLabelText('名字')
    expect(input).toHaveFocus()
    const dialog = screen.getByRole('dialog')
    // 最后一个按钮上 Tab 回到第一个；第一个上 Shift+Tab 到最后一个
    screen.getByRole('button', { name: '确定' }).focus()
    fireEvent.keyDown(dialog, { key: 'Tab' })
    expect(input).toHaveFocus()
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true })
    expect(screen.getByRole('button', { name: '确定' })).toHaveFocus()
    // 中间的元素交给浏览器默认行为，不拦截
    screen.getByRole('button', { name: '取消' }).focus()
    expect(fireEvent.keyDown(dialog, { key: 'Tab' })).toBe(true)
    // Esc 关闭后焦点回到“打开”按钮
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })
})

describe('Modal behaviour shared by dialogs and drawers', () => {
  it('closes on Esc and on overlay click, but not on clicks inside', () => {
    const onClose = vi.fn()
    const { container } = render(<Modal title="框" onClose={onClose}><button type="button">里面</button></Modal>)
    fireEvent.click(screen.getByRole('button', { name: '里面' }))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.click(container.querySelector('.modal-overlay') as HTMLElement)
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('locks page scroll while open and restores it after the last modal closes', () => {
    document.body.style.overflow = 'auto'
    const first = render(<Modal title="一" onClose={() => undefined}><button type="button">a</button></Modal>)
    expect(document.body.style.overflow).toBe('hidden')
    const second = render(<Modal title="二" onClose={() => undefined}><button type="button">b</button></Modal>)
    first.unmount()
    expect(document.body.style.overflow).toBe('hidden')  // 还有一个没关
    second.unmount()
    expect(document.body.style.overflow).toBe('auto')
    document.body.style.overflow = ''
  })

  it('also locks the real scroll container (.pf-body) and restores it', () => {
    const body = document.createElement('div')
    body.className = 'pf-body'
    body.style.overflow = 'auto'
    document.body.appendChild(body)
    const view = render(<Modal title="框" onClose={() => undefined}><button type="button">a</button></Modal>)
    expect(body.style.overflow).toBe('hidden')
    view.unmount()
    expect(body.style.overflow).toBe('auto')
    body.remove()
  })

  it('focuses the dialog itself when it has nothing focusable, and keeps Tab inside', () => {
    render(<Modal title="空框" onClose={() => undefined}><p>只有文字</p></Modal>)
    const dialog = screen.getByRole('dialog', { name: '空框' })
    expect(dialog).toHaveFocus()
    expect(fireEvent.keyDown(dialog, { key: 'Tab' })).toBe(false)  // 被拦截，焦点不会跑出去
  })

  it('renders a drawer variant with a labelled close button as the first focus target', () => {
    const onClose = vi.fn()
    const { container } = render(<Modal title="历史" variant="drawer" closeLabel="关闭历史" onClose={onClose}><p>内容</p></Modal>)
    expect(container.querySelector('.modal-overlay--drawer')).not.toBeNull()
    expect(container.querySelector('.modal--drawer')).not.toBeNull()
    const close = screen.getByRole('button', { name: '关闭历史' })
    expect(close).toHaveFocus()
    fireEvent.click(close)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
