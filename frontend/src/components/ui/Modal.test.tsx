import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
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
