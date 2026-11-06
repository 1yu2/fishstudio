import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsPage from './SettingsPage'

vi.mock('./PersonalizationPanel', () => ({ default: () => <div>PERSONAL</div> }))
vi.mock('./modelServices/ModelServicesPanel', () => ({ default: () => <div>MODELS</div> }))
vi.mock('react-markdown', () => ({ default: ({ children }: { children: string }) => <div>{children}</div> }))

const NOTE = '只有管理员可以修改这些全局设置'

function setUser(isAdmin: boolean) {
  localStorage.setItem('fishstudio:auth_user', JSON.stringify({ id: 1, username: 'u', is_admin: isAdmin }))
  localStorage.setItem('fishstudio:auth_token', 't')
}

function mockFetch(putStatus = 200) {
  const calls: { url: string; method: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({ url, method })
      if (method === 'PUT') return new Response('{}', { status: putStatus })
      let body: unknown = {}
      if (url.endsWith('/api/settings/workspace')) body = ['SOUL', 'USER', 'MEMORY'].map((name) => ({ id: `${name}.md`, name, content: 'hi' }))
      if (url.endsWith('/api/settings/skills')) body = { skills: [{ id: 't1', name: '工具一', description: 'd', enabled: true, category: 'image' }] }
      if (url.endsWith('/api/settings/skills/installed')) body = [{ id: 's1', name: 'Skill', description: 'd', enabled: true }]
      if (url.endsWith('/api/settings/mcp')) body = { mcpServers: { a: { command: 'npx', args: [] } } }
      return new Response(JSON.stringify(body), { status: 200 })
    }),
  )
  return calls
}

async function openTab(label: string) {
  fireEvent.click(screen.getByRole('button', { name: new RegExp(label) }))
}

describe('SettingsPage global settings are admin-only', () => {
  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('non-admin sees read-only workspace editor with the admin-only note', async () => {
    setUser(false)
    mockFetch()
    render(<SettingsPage theme="dark" onToggleTheme={() => undefined} managedByAppShell />)
    expect(await screen.findByTestId('admin-only-note')).toHaveTextContent(NOTE)
    const textarea = await screen.findByPlaceholderText(/在此编辑/)
    expect(textarea).toHaveAttribute('readonly')
    expect(screen.getByRole('button', { name: /保存/ })).toBeDisabled()
  })

  it.each(['USER.md', 'MEMORY.md'])('non-admin can edit and save own %s, then global files stay read-only', async (fileId) => {
    setUser(false)
    const calls = mockFetch()
    render(<SettingsPage theme="dark" onToggleTheme={() => undefined} managedByAppShell />)
    fireEvent.click(await screen.findByText(fileId))
    const textarea = screen.getByPlaceholderText(/在此编辑/)
    expect(textarea).not.toHaveAttribute('readonly')
    expect(screen.queryByTestId('admin-only-note')).toBeNull()
    fireEvent.change(textarea, { target: { value: 'my private notes' } })
    fireEvent.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() => expect(calls).toContainEqual({ url: `/api/settings/workspace/${fileId}`, method: 'PUT' }))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
    fireEvent.click(screen.getByText('SOUL.md'))
    expect(screen.getByPlaceholderText(/在此编辑/)).toHaveAttribute('readonly')
    expect(screen.getByRole('button', { name: /保存/ })).toBeDisabled()
  })

  it('non-admin: tools, skills and MCP controls are disabled', async () => {
    setUser(false)
    const calls = mockFetch()
    render(<SettingsPage theme="dark" onToggleTheme={() => undefined} managedByAppShell />)

    await openTab('工具')
    expect(await screen.findByTestId('admin-only-note')).toHaveTextContent(NOTE)
    expect(await screen.findByText('工具一')).toBeInTheDocument()
    for (const box of screen.getAllByRole('checkbox')) expect(box).toBeDisabled()
    expect(screen.getByRole('button', { name: /保存/ })).toBeDisabled()

    await openTab('Skills')
    expect(await screen.findByTestId('admin-only-note')).toHaveTextContent(NOTE)
    await screen.findByText('Skill')
    for (const box of screen.getAllByRole('checkbox')) expect(box).toBeDisabled()

    await openTab('MCP')
    expect(await screen.findByTestId('admin-only-note')).toHaveTextContent(NOTE)
    await waitFor(() => expect(screen.getByRole('button', { name: /保存/ })).toBeDisabled())
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
  })

  it('admin can edit and save', async () => {
    setUser(true)
    const calls = mockFetch()
    render(<SettingsPage theme="dark" onToggleTheme={() => undefined} managedByAppShell />)
    const textarea = await screen.findByPlaceholderText(/在此编辑/)
    expect(textarea).not.toHaveAttribute('readonly')
    expect(screen.queryByTestId('admin-only-note')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /保存/ }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true))
    expect(await screen.findByText('已保存')).toBeInTheDocument()
  })

  it('a 403 from the server shows the admin-only message instead of a generic failure', async () => {
    setUser(true) // 本地缓存说是管理员，但服务端拒绝（例如角色刚被收回）
    mockFetch(403)
    render(<SettingsPage theme="dark" onToggleTheme={() => undefined} managedByAppShell />)
    await screen.findByPlaceholderText(/在此编辑/)
    fireEvent.click(screen.getByRole('button', { name: /保存/ }))
    expect(await screen.findByText(NOTE)).toBeInTheDocument()
    expect(screen.queryByText('保存失败，请重试')).toBeNull()
  })
})
