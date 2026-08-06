import { useCallback, useEffect, useRef, useState } from 'react'
import * as seriesApi from '../api/series'
import type { SeriesProject, StoryboardData } from '../api/series'

/** 加载项目详情 + 分镜结构，并提供刷新方法。 */
export function useSeriesProject(projectId: string | null) {
  const [project, setProject] = useState<SeriesProject | null>(null)
  const [storyboard, setStoryboard] = useState<StoryboardData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const reload = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    setError(null)
    try {
      const [p, sb] = await Promise.all([
        seriesApi.getProject(projectId),
        seriesApi.getStoryboard(projectId).catch(() => null),
      ])
      if (!mountedRef.current) return
      setProject(p)
      setStoryboard(sb)
    } catch (e) {
      if (!mountedRef.current) return
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void reload()
  }, [reload])

  return { project, storyboard, loading, error, reload }
}
