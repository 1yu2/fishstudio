import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '../api'
import { acquireMediaUrl } from './media'

vi.mock('../api', () => ({ apiFetch: vi.fn(), getToken: vi.fn() }))

describe('protected media object URLs', () => {
  let created = 0
  const revoke = vi.fn()

  beforeEach(() => {
    vi.useFakeTimers()
    created = 0
    revoke.mockClear()
    vi.mocked(apiFetch).mockReset()
    vi.mocked(apiFetch).mockImplementation(async () => new Response(new Blob(['mp4']), { status: 200 }))
    URL.createObjectURL = vi.fn(() => `blob:test/${++created}`)
    URL.revokeObjectURL = revoke
  })
  afterEach(() => { vi.useRealTimers() })

  it('keeps the URL alive while another component still uses it', async () => {
    const a = acquireMediaUrl('/api/a')
    const b = acquireMediaUrl('/api/a')
    const first = await a.promise
    expect(await b.promise).toBe(first)
    expect(apiFetch).toHaveBeenCalledTimes(1)

    a.release()
    a.release() // 重复归还不影响别人
    await vi.runAllTimersAsync()
    expect(revoke).not.toHaveBeenCalled()

    b.release()
    await vi.runAllTimersAsync()
    expect(revoke).toHaveBeenCalledWith(first)
  })

  it('survives an immediate unmount and remount without downloading again', async () => {
    const first = acquireMediaUrl('/api/b')
    const url = await first.promise
    first.release()
    const again = acquireMediaUrl('/api/b')
    await vi.runAllTimersAsync()
    expect(await again.promise).toBe(url)
    expect(revoke).not.toHaveBeenCalled()
    expect(apiFetch).toHaveBeenCalledTimes(1)
    again.release()
  })

  it('a failed lease never releases a newer download of the same URL', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response('', { status: 404 }))
    const failed = acquireMediaUrl('/api/c')
    await expect(failed.promise).rejects.toThrow('404')
    const fresh = acquireMediaUrl('/api/c')
    const url = await fresh.promise
    failed.release()
    await vi.runAllTimersAsync()
    expect(revoke).not.toHaveBeenCalledWith(url)
    expect(apiFetch).toHaveBeenCalledTimes(2)
    fresh.release()
  })
})
