import { useCallback, useEffect, useRef, useState } from 'react'
import * as seriesApi from '../api/series'
import type { WorkflowRun } from '../api/series'

const ACTIVE_STEP_STATUSES = new Set(['ready', 'queued', 'running', 'waiting_approval'])
const POLL_INTERVAL = 5000

/**
 * 加载某个项目最新的 workflow run；存在活跃 step 时 5 秒轮询。
 * active 为 false（如项目页未打开）时不轮询。
 */
export function useWorkflowRun(projectId: string | null, active = true) {
  const [run, setRun] = useState<WorkflowRun | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const clearTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }

  const load = useCallback(async () => {
    if (!projectId) return
    setError(null)
    try {
      const runs = await seriesApi.listRuns(projectId)
      if (!mountedRef.current) return
      // 后端 list_runs 按 created_at DESC 返回，runs[0] 即最新 run
      const latest = Array.isArray(runs) && runs.length > 0 ? runs[0] : null
      if (latest) {
        const detail = await seriesApi.getRun(latest.id)
        if (!mountedRef.current) return
        setRun(detail)
        return
      }
      setRun(null)
    } catch (e) {
      if (mountedRef.current) setError(e instanceof Error ? e.message : '加载失败')
    }
  }, [projectId])

  const reload = useCallback(async () => {
    setLoading(true)
    await load()
    if (mountedRef.current) setLoading(false)
  }, [load])

  useEffect(() => {
    clearTimer()
    if (!projectId || !active) return
    void reload()

    const tick = async () => {
      await load()
      if (!mountedRef.current) return
      setRun((current) => {
        const hasActive = current?.steps?.some((s) => ACTIVE_STEP_STATUSES.has(s.status)) ?? false
        if (hasActive) {
          timerRef.current = window.setTimeout(() => void tick(), POLL_INTERVAL)
        }
        return current
      })
    }
    timerRef.current = window.setTimeout(() => void tick(), POLL_INTERVAL)
    return clearTimer
  }, [projectId, active, load, reload])

  return { run, loading, error, reload }
}
