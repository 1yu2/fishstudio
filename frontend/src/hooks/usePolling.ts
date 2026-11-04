import { useCallback, useEffect, useRef, useState } from 'react'

export const POLL_INTERVAL_MS = 10_000

/**
 * 加载一份数据并每 10 秒轮询；页面不可见时暂停，重新可见时立即刷新。
 * 只让最新发出的请求更新状态，避免慢的旧响应覆盖新数据；fetcher 变化（如切换项目）会重新加载。
 * 错误带上 HTTP 状态码（若抛出的 Error 上有 status 字段），调用方可区分 404 等情况。
 */
export function usePolling<T>(fetcher: () => Promise<T>, fallbackError: string) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [errorStatus, setErrorStatus] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const alive = useRef(true)
  const seq = useRef(0)

  const load = useCallback(async () => {
    const id = ++seq.current
    try {
      const next = await fetcher()
      if (!alive.current || id !== seq.current) return
      setData(next)
      setError(null)
      setErrorStatus(null)
    } catch (e) {
      if (alive.current && id === seq.current) {
        setError(e instanceof Error && e.message ? e.message : fallbackError)
        setErrorStatus(typeof (e as { status?: unknown })?.status === 'number' ? (e as { status: number }).status : null)
      }
    } finally {
      if (alive.current && id === seq.current) setLoading(false)
    }
  }, [fetcher, fallbackError])

  const retry = useCallback(() => {
    setLoading(true)
    setError(null)
    setErrorStatus(null)
    void load()
  }, [load])

  useEffect(() => {
    alive.current = true
    // fetcher 变化意味着换了数据源，旧数据不能继续展示
    setData(null)
    setError(null)
    setErrorStatus(null)
    setLoading(true)
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

  return { data, error, errorStatus, loading, retry }
}
