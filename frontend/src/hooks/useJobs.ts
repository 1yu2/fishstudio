import { useCallback, useEffect, useRef, useState } from 'react'
import * as seriesApi from '../api/series'
import type { Job } from '../api/series'

const ACTIVE_JOB_STATUSES = new Set(['pending', 'leased', 'running'])
const POLL_INTERVAL = 5000

/** 项目任务列表；存在活跃任务且 active 时 5 秒轮询。 */
export function useJobs(projectId: string | null, active = true) {
  const [jobs, setJobs] = useState<Job[]>([])
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
    try {
      const list = await seriesApi.listJobs({ project_id: projectId })
      if (mountedRef.current) {
        setJobs(Array.isArray(list) ? list : [])
        setError(null)
      }
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
    if (!projectId || !active) return
    void reload()
  }, [projectId, active, reload])

  useEffect(() => {
    clearTimer()
    if (!projectId || !active || !jobs.some((job) => ACTIVE_JOB_STATUSES.has(job.status))) {
      return
    }
    timerRef.current = window.setTimeout(() => void load(), POLL_INTERVAL)
    return clearTimer
  }, [projectId, active, jobs, load])

  const retry = useCallback(
    async (jobId: string) => {
      await seriesApi.retryJob(jobId)
      await load()
    },
    [load],
  )

  const cancel = useCallback(
    async (jobId: string) => {
      await seriesApi.cancelJob(jobId)
      await load()
    },
    [load],
  )

  return { jobs, loading, error, reload, retry, cancel }
}
