import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { libraryApi, type LibraryCharacter } from '../../api/library'
import { confirmDialog } from '../ui/ConfirmDialog'
import LibraryPage from './LibraryPage'

vi.mock('../../api/library', () => ({
  libraryApi: { list: vi.fn(), upload: vi.fn(), update: vi.fn(), remove: vi.fn(), saveToLibrary: vi.fn(), addToProject: vi.fn() },
}))
vi.mock('../ui/ConfirmDialog', () => ({ confirmDialog: vi.fn() }))
vi.mock('../ui/Toast', () => ({ toast: vi.fn() }))
vi.mock('../common/ProtectedMedia', () => ({
  ProtectedImage: ({ src, alt }: { src: string; alt?: string }) => <img src={src} alt={alt ?? ''} />,
}))

const lib = vi.mocked(libraryApi)
const item = (id: string, name: string, description: string, looks = 1): LibraryCharacter => ({
  id, name, description, updated_at: null, in_project: false,
  looks: Array.from({ length: looks }, (_, index) => ({ name: index ? `造型${index}` : '默认', artifact_id: `${id}-${index}`, url: `/api/artifacts/${id}-${index}/download` })),
  cover_url: `/api/artifacts/${id}-0/download`,
})

beforeEach(() => {
  vi.clearAllMocks()
  lib.list.mockResolvedValue([item('a', '狐妖', '白发金瞳', 2), item('b', '书生', '青衫')])
})

describe('LibraryPage', () => {
  it('shows the empty state when there are no characters', async () => {
    lib.list.mockResolvedValue([])
    render(<LibraryPage />)
    expect(await screen.findByText('角色库还是空的')).toBeInTheDocument()
  })

  it('lists characters with cover and look count, and filters by search', async () => {
    render(<LibraryPage />)
    expect(await screen.findByText('狐妖')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '狐妖封面' })).toHaveAttribute('src', '/api/artifacts/a-0/download')
    expect(screen.getByText(/2 套造型/)).toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索角色' }), { target: { value: '青衫' } })
    expect(screen.queryByText('狐妖')).not.toBeInTheDocument()
    expect(screen.getByText('书生')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索角色' }), { target: { value: 'zzz' } })
    expect(screen.getByText('没有匹配的角色')).toBeInTheDocument()
  })

  it('uploads a character image and reloads the list', async () => {
    lib.upload.mockResolvedValue(item('c', '小满', ''))
    render(<LibraryPage />)
    await screen.findByText('狐妖')
    fireEvent.click(screen.getByRole('button', { name: /上传自己的角色图/ }))
    const dialog = await screen.findByRole('dialog', { name: '上传自己的角色图' })
    fireEvent.change(within(dialog).getByLabelText('角色名'), { target: { value: '小满' } })
    const file = new File(['x'], 'a.webp', { type: 'image/webp' })
    fireEvent.change(within(dialog).getByLabelText('角色图'), { target: { files: [file] } })
    const before = lib.list.mock.calls.length
    fireEvent.click(within(dialog).getByRole('button', { name: '上传' }))
    await waitFor(() => expect(lib.upload).toHaveBeenCalledWith({ name: '小满', description: '', file }))
    await waitFor(() => expect(lib.list.mock.calls.length).toBe(before + 1))
  })

  it('renames and re-describes a character', async () => {
    lib.update.mockResolvedValue(item('a', '九尾', '新描述', 2))
    render(<LibraryPage />)
    await screen.findByText('狐妖')
    fireEvent.click(screen.getByRole('button', { name: '编辑 狐妖' }))
    const dialog = await screen.findByRole('dialog', { name: '编辑角色' })
    fireEvent.change(within(dialog).getByLabelText('角色名'), { target: { value: '九尾' } })
    fireEvent.change(within(dialog).getByLabelText('外貌描述'), { target: { value: '新描述' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))
    await waitFor(() => expect(lib.update).toHaveBeenCalledWith('a', { name: '九尾', description: '新描述' }))
    expect(await screen.findByText('九尾')).toBeInTheDocument()
    expect(screen.queryByText('狐妖')).not.toBeInTheDocument()
  })

  it('deletes only after confirming', async () => {
    vi.mocked(confirmDialog).mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    lib.remove.mockResolvedValue(undefined)
    render(<LibraryPage />)
    await screen.findByText('狐妖')
    fireEvent.click(screen.getByRole('button', { name: '删除 狐妖' }))
    await waitFor(() => expect(confirmDialog).toHaveBeenCalledTimes(1))
    expect(lib.remove).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '删除 狐妖' }))
    await waitFor(() => expect(lib.remove).toHaveBeenCalledWith('a'))
    await waitFor(() => expect(screen.queryByText('狐妖')).not.toBeInTheDocument())
    expect(vi.mocked(confirmDialog).mock.calls[0][0]).toMatchObject({ danger: true })
  })
})
