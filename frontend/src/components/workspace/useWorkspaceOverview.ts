import { useCallback, useEffect, useRef, useState } from 'react'
import { workspaceApi, type WorkspaceOverview } from '../../api/workspace'

export const POLL_INTERVAL_MS = 10_000

/** 加载工作台总览，每 10 秒轮询一次；页面不可见时暂停，重新可见时立即刷新。 */
export function useWorkspaceOverview() {
  const [data, setData] = useState<WorkspaceOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const alive = useRef(true)
  const seq = useRef(0)

  const load = useCallback(async () => {
    // 只让最新发出的请求更新状态，避免慢的旧响应覆盖新数据
    const id = ++seq.current
    try {
      const next = await workspaceApi.overview()
      if (!alive.current || id !== seq.current) return
      setData(next)
      setError(null)
    } catch (e) {
      if (alive.current && id === seq.current) setError(e instanceof Error ? e.message : '工作台加载失败')
    } finally {
      if (alive.current && id === seq.current) setLoading(false)
    }
  }, [])

  const retry = useCallback(() => {
    setLoading(true)
    setError(null)
    void load()
  }, [load])

  useEffect(() => {
    alive.current = true
    void load()
    let timer: number | undefined
    const stop = () => {
      if (timer !== undefined) window.clearInterval(timer)
      timer = undefined
    }
    const start = () => {
      stop()
      timer = window.setInterval(() => void load(), POLL_INTERVAL_MS)
    }
    const onVisibility = () => {
      if (document.hidden) {
        stop()
      } else {
        void load()
        start()
      }
    }
    if (!document.hidden) start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      alive.current = false
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [load])

  return { data, error, loading, retry }
}
